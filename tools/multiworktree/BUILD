load("@rules_python//python:defs.bzl", "py_binary", "py_library", "py_test")

package(default_visibility = ["//visibility:public"])

filegroup(
    name = "srcs",
    srcs = glob(
        ["**"],
        exclude = ["**/__pycache__/**"],
    ),
)

py_library(
    name = "supervisor",
    srcs = ["bazel_multiworktree.py"],
    imports = ["."],
)

py_binary(
    name = "bazel_multiworktree",
    srcs = ["bazel_multiworktree.py"],
    legacy_create_init = False,
    main = "bazel_multiworktree.py",
)

py_test(
    name = "bazel_multiworktree_test",
    srcs = ["bazel_multiworktree_test.py"],
    legacy_create_init = False,
    target_compatible_with = ["@platforms//os:linux"],
    deps = [
        ":supervisor",
        "//third_party/py/abseil",
    ],
)
