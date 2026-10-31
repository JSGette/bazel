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

#include <fcntl.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cstdlib>
#include <filesystem>
#include <string>
#include <vector>

#include "googletest/include/gtest/gtest.h"

namespace blaze {
namespace {
class SharedServerOwnershipTest : public testing::Test {
 protected:
  void SetUp() override {
    std::string pattern =
        std::string(getenv("TEST_TMPDIR")) + "/ownership-XXXXXX";
    std::vector<char> temp(pattern.begin(), pattern.end());
    temp.push_back(0);
    ASSERT_NE(mkdtemp(temp.data()), nullptr);
    root = temp.data();
    control = Directory("control");
    output = Directory("output");
  }
  void TearDown() override { std::filesystem::remove_all(root); }
  std::string Directory(const std::string& name) {
    std::string path = root + "/" + name;
    std::filesystem::create_directory(path);
    return path;
  }
  std::unique_ptr<SharedServerOwnership> Acquire(const std::string& c,
                                                 const std::string& o) {
    return SharedServerOwnership::Acquire(c, o, &error);
  }
  std::string root, control, output, error;
};

TEST_F(SharedServerOwnershipTest, CollisionAndPartialAcquisitionRollback) {
  auto owner = Acquire(control, output);
  ASSERT_NE(owner, nullptr) << error;
  std::string other_control = Directory("other-control");
  std::string other_output = Directory("other-output");
  EXPECT_EQ(Acquire(control, other_output), nullptr);
  EXPECT_EQ(Acquire(other_control, output), nullptr);
  auto independent = Acquire(other_control, other_output);
  EXPECT_NE(independent, nullptr) << error;
  owner.reset();
  EXPECT_NE(Acquire(control, output), nullptr) << error;
}

TEST_F(SharedServerOwnershipTest,
       OutputPathAndPhysicalObjectRemainReservedAfterRename) {
  auto owner = Acquire(control, output);
  ASSERT_NE(owner, nullptr) << error;
  std::string moved = root + "/moved-output";
  std::filesystem::rename(output, moved);
  std::filesystem::create_directory(output);
  std::string other_control = Directory("other-control");
  EXPECT_EQ(Acquire(other_control, output), nullptr);
  EXPECT_EQ(Acquire(other_control, moved), nullptr);
  owner.reset();
  EXPECT_NE(Acquire(other_control, moved), nullptr) << error;
}

TEST_F(SharedServerOwnershipTest,
       ControlPathAndPhysicalObjectRemainReservedAfterRename) {
  auto owner = Acquire(control, output);
  ASSERT_NE(owner, nullptr) << error;
  std::string moved = root + "/moved-control";
  std::filesystem::rename(control, moved);
  std::filesystem::create_directory(control);
  std::string other_output = Directory("other-output");
  EXPECT_EQ(Acquire(control, other_output), nullptr);
  EXPECT_EQ(Acquire(moved, other_output), nullptr);
}

TEST_F(SharedServerOwnershipTest, RejectsUnpinnablePathsAndRollsBack) {
  EXPECT_EQ(Acquire("relative", output), nullptr);
  EXPECT_EQ(Acquire(control, root + "/missing"), nullptr);
  std::string alias = root + "/alias";
  ASSERT_EQ(symlink(control.c_str(), alias.c_str()), 0);
  EXPECT_EQ(Acquire(alias, output), nullptr);
  EXPECT_NE(Acquire(control, output), nullptr) << error;
}

TEST_F(SharedServerOwnershipTest, AdoptionValidatesHandoffAndSetsCloseOnExec) {
  auto owner = Acquire(control, output);
  ASSERT_NE(owner, nullptr) << error;
  for (int fd : owner->descriptors())
    EXPECT_EQ(fcntl(fd, F_GETFD) & FD_CLOEXEC, 0);
  ASSERT_TRUE(SharedServerOwnership::Adopt(owner->descriptors(), control,
                                           output, &error))
      << error;
  for (int fd : owner->descriptors())
    EXPECT_NE(fcntl(fd, F_GETFD) & FD_CLOEXEC, 0);
  EXPECT_EQ(Acquire(control, output), nullptr);
}

TEST_F(SharedServerOwnershipTest,
       AdoptionRejectsMissingDuplicateAndWrongDescriptors) {
  auto owner = Acquire(control, output);
  ASSERT_NE(owner, nullptr) << error;
  EXPECT_FALSE(SharedServerOwnership::Adopt({}, control, output, &error));
  auto duplicate = owner->descriptors();
  duplicate[0] = duplicate[1];
  EXPECT_FALSE(
      SharedServerOwnership::Adopt(duplicate, control, output, &error));
  EXPECT_FALSE(SharedServerOwnership::Adopt(owner->descriptors(), output,
                                            control, &error));
  std::filesystem::rename(output, root + "/old-output");
  std::filesystem::create_directory(output);
  EXPECT_FALSE(SharedServerOwnership::Adopt(owner->descriptors(), control,
                                            output, &error));
}

TEST_F(SharedServerOwnershipTest,
       ParentReleaseDoesNotReleaseInheritedOwnership) {
  auto owner = Acquire(control, output);
  ASSERT_NE(owner, nullptr) << error;
  int barrier[2];
  ASSERT_EQ(pipe(barrier), 0);
  pid_t child = fork();
  ASSERT_GE(child, 0);
  if (child == 0) {
    close(barrier[1]);
    char token;
    read(barrier[0], &token, 1);
    _exit(0);
  }
  close(barrier[0]);
  owner.reset();
  bool blocked = Acquire(control, output) == nullptr;
  close(barrier[1]);
  int status;
  ASSERT_EQ(waitpid(child, &status, 0), child);
  EXPECT_EQ(status, 0);
  EXPECT_TRUE(blocked);
  EXPECT_NE(Acquire(control, output), nullptr) << error;
}

TEST_F(SharedServerOwnershipTest, AbruptOwnerDeathReleasesReservation) {
  int barrier[2];
  ASSERT_EQ(pipe(barrier), 0);
  pid_t child = fork();
  ASSERT_GE(child, 0);
  if (child == 0) {
    close(barrier[0]);
    auto owner = Acquire(control, output);
    char token = owner ? 'y' : 'n';
    write(barrier[1], &token, 1);
    close(barrier[1]);
    for (;;) pause();
  }
  close(barrier[1]);
  char token = 0;
  ssize_t count = read(barrier[0], &token, 1);
  close(barrier[0]);
  bool blocked = Acquire(control, output) == nullptr;
  kill(child, SIGKILL);
  int status;
  ASSERT_EQ(waitpid(child, &status, 0), child);
  EXPECT_EQ(count, 1);
  EXPECT_EQ(token, 'y');
  EXPECT_TRUE(blocked);
  EXPECT_TRUE(WIFSIGNALED(status));
  EXPECT_NE(Acquire(control, output), nullptr) << error;
}

TEST_F(SharedServerOwnershipTest, ExecutedChildrenDoNotRetainOwnership) {
  auto owner = Acquire(control, output);
  ASSERT_NE(owner, nullptr) << error;
  ASSERT_TRUE(SharedServerOwnership::Adopt(owner->descriptors(), control,
                                           output, &error));
  int barrier[2];
  ASSERT_EQ(pipe2(barrier, O_CLOEXEC), 0);
  pid_t child = fork();
  ASSERT_GE(child, 0);
  if (child == 0) {
    close(barrier[0]);
    execl("/bin/sleep", "sleep", "30", nullptr);
    _exit(127);
  }
  close(barrier[1]);
  char token;
  ssize_t count =
      read(barrier[0], &token, 1);  // EOF after exec closes the pipe.
  close(barrier[0]);
  owner.reset();
  auto replacement = Acquire(control, output);
  bool child_alive = kill(child, 0) == 0;
  kill(child, SIGKILL);
  int status;
  ASSERT_EQ(waitpid(child, &status, 0), child);
  EXPECT_EQ(count, 0);
  EXPECT_TRUE(child_alive);
  EXPECT_TRUE(WIFSIGNALED(status));
  EXPECT_NE(replacement, nullptr) << error;
}
}  // namespace
}  // namespace blaze
