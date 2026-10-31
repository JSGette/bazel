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
// Process-lifetime ownership for the Linux multi-engine shared-server
// prototype.
#ifndef BAZEL_SRC_MAIN_CPP_SHARED_SERVER_OWNERSHIP_H_
#define BAZEL_SRC_MAIN_CPP_SHARED_SERVER_OWNERSHIP_H_

#include <memory>
#include <string>
#include <vector>

namespace blaze {

// Reserves both paths and pinned directory objects using Linux abstract
// sockets. No lock file can be unlinked to bypass this reservation.
// Coordination is local to one network namespace; this is not a cross-host or
// security boundary.
class SharedServerOwnership {
 public:
  static std::unique_ptr<SharedServerOwnership> Acquire(
      const std::string& control, const std::string& output,
      std::string* error);
  static std::unique_ptr<SharedServerOwnership> AcquireEngine(
      const std::string& output, std::string* error);
  ~SharedServerOwnership();
  SharedServerOwnership(const SharedServerOwnership&) = delete;
  SharedServerOwnership& operator=(const SharedServerOwnership&) = delete;

  // Descriptors intentionally survive daemonize/JVM exec. The launcher closes
  // its copies after spawn. Adopt validates them and prevents action
  // inheritance.
  const std::vector<int>& descriptors() const { return descriptors_; }
  static bool Adopt(const std::vector<int>& descriptors,
                    const std::string& control, const std::string& output,
                    std::string* error);

 private:
  SharedServerOwnership() = default;
  bool Reserve(const std::string& path, std::string* error);
  std::vector<int> descriptors_;
};

}  // namespace blaze
#endif  // BAZEL_SRC_MAIN_CPP_SHARED_SERVER_OWNERSHIP_H_
