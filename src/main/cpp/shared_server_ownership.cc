// Copyright 2026 The Bazel Authors. All rights reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
#include "src/main/cpp/shared_server_ownership.h"

#include <set>

#ifdef __linux__
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include "src/main/cpp/util/md5.h"
#endif

namespace blaze {
#ifdef __linux__
namespace {

std::string PathKey(const std::string& path) {
  blaze_util::Md5Digest digest;
  digest.Update(path.data(), path.size());
  unsigned char bytes[blaze_util::Md5Digest::kDigestLength];
  digest.Finish(bytes);
  return "path:" + digest.String();
}

std::string ObjectKey(const struct stat& st) {
  return "object:" + std::to_string(static_cast<uint64_t>(st.st_dev)) + ":" +
         std::to_string(static_cast<uint64_t>(st.st_ino));
}

socklen_t Address(const std::string& key, struct sockaddr_un* address) {
  *address = {};
  address->sun_family = AF_UNIX;
  const std::string name = "bazel-shared-owner-v1:" + key;
  // Both key encodings have a fixed bound below sun_path's capacity.
  memcpy(address->sun_path + 1, name.data(), name.size());
  return offsetof(struct sockaddr_un, sun_path) + 1 + name.size();
}

bool MatchesSocket(int fd, const std::string& key) {
  struct sockaddr_un expected, actual;
  socklen_t expected_size = Address(key, &expected);
  socklen_t actual_size = sizeof(actual);
  return getsockname(fd, reinterpret_cast<struct sockaddr*>(&actual),
                     &actual_size) == 0 &&
         actual_size == expected_size &&
         memcmp(&actual, &expected, actual_size) == 0;
}

int ReserveSocket(const std::string& key, std::string* error) {
  int fd = socket(AF_UNIX, SOCK_STREAM, 0);
  if (fd < 0) {
    *error = "Cannot create shared ownership socket: " +
             std::string(strerror(errno));
    return -1;
  }
  struct sockaddr_un address;
  socklen_t size = Address(key, &address);
  if (bind(fd, reinterpret_cast<struct sockaddr*>(&address), size) != 0) {
    *error = errno == EADDRINUSE ? "Shared server ownership is held; refusing "
                                   "to replace its control or engine state"
                                 : "Cannot reserve shared ownership: " +
                                       std::string(strerror(errno));
    close(fd);
    return -1;
  }
  return fd;
}

bool DirectoryMatches(int fd, const std::string& path, struct stat* pinned) {
  struct stat current;
  return fstat(fd, pinned) == 0 && S_ISDIR(pinned->st_mode) &&
         lstat(path.c_str(), &current) == 0 && S_ISDIR(current.st_mode) &&
         pinned->st_dev == current.st_dev && pinned->st_ino == current.st_ino;
}

}  // namespace
#endif

std::unique_ptr<SharedServerOwnership> SharedServerOwnership::Acquire(
    const std::string& control, const std::string& output, std::string* error) {
  std::unique_ptr<SharedServerOwnership> result(new SharedServerOwnership());
  if (!result->Reserve(control, error) || !result->Reserve(output, error)) {
    return nullptr;
  }
  return result;
}

std::unique_ptr<SharedServerOwnership> SharedServerOwnership::AcquireEngine(
    const std::string& output, std::string* error) {
  std::unique_ptr<SharedServerOwnership> result(new SharedServerOwnership());
  if (!result->Reserve(output, error)) return nullptr;
#ifdef __linux__
  for (int fd : result->descriptors_) {
    if (fcntl(fd, F_SETFD, FD_CLOEXEC) < 0) {
      *error = "Cannot protect engine ownership descriptor";
      return nullptr;
    }
  }
#endif
  return result;
}

bool SharedServerOwnership::Reserve(const std::string& path,
                                    std::string* error) {
#ifdef __linux__
  if (path.empty() || path[0] != '/') {
    *error = "Shared ownership requires absolute directory paths";
    return false;
  }
  int logical = ReserveSocket(PathKey(path), error);
  if (logical < 0) return false;
  descriptors_.push_back(logical);
  int directory = open(path.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
  if (directory < 0) {
    *error = "Cannot pin shared ownership directory: " +
             std::string(strerror(errno));
    return false;
  }
  descriptors_.push_back(directory);
  struct stat pinned;
  if (!DirectoryMatches(directory, path, &pinned)) {
    *error = "Shared ownership directory changed while opening";
    return false;
  }
  int physical = ReserveSocket(ObjectKey(pinned), error);
  if (physical < 0) return false;
  descriptors_.push_back(physical);
  return true;
#else
  *error = "Shared server ownership is supported only on Linux";
  return false;
#endif
}

SharedServerOwnership::~SharedServerOwnership() {
#ifdef __linux__
  for (int fd : descriptors_) close(fd);
#endif
}

bool SharedServerOwnership::Adopt(const std::vector<int>& descriptors,
                                  const std::string& control,
                                  const std::string& output,
                                  std::string* error) {
#ifdef __linux__
  if (descriptors.size() != 6 ||
      std::set<int>(descriptors.begin(), descriptors.end()).size() != 6) {
    *error = "Shared ownership handoff requires six distinct descriptors";
    return false;
  }
  for (int fd : descriptors) {
    if (fd < 3) {
      *error = "Invalid shared ownership descriptor";
      return false;
    }
  }
  const std::string paths[] = {control, output};
  for (int i = 0; i < 2; ++i) {
    struct stat pinned;
    if (!DirectoryMatches(descriptors[3 * i + 1], paths[i], &pinned) ||
        !MatchesSocket(descriptors[3 * i], PathKey(paths[i])) ||
        !MatchesSocket(descriptors[3 * i + 2], ObjectKey(pinned))) {
      *error =
          "Shared ownership handoff does not match its directory paths/objects";
      return false;
    }
  }
  for (int fd : descriptors) {
    int flags = fcntl(fd, F_GETFD);
    if (flags < 0 || fcntl(fd, F_SETFD, flags | FD_CLOEXEC) < 0) {
      *error = "Cannot prevent shared ownership inheritance by actions";
      return false;
    }
  }
  // Intentionally retain these descriptors until process exit, including failed
  // initialization, cleanup and JVM shutdown hooks. No successful engine
  // disposal is inferred from a Java resource scope ending.
  return true;
#else
  *error = "Shared server ownership is supported only on Linux";
  return false;
#endif
}
}  // namespace blaze
