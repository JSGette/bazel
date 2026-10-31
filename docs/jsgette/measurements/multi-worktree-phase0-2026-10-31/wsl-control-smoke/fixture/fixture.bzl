load("@selected//:value.bzl", "MODULE_VALUE")

VERSION = "base-bzl"

def _toolchain_impl(ctx):
    return [platform_common.ToolchainInfo(message = ctx.attr.message)]

message_toolchain = rule(
    implementation = _toolchain_impl,
    attrs = {"message": attr.string(mandatory = True)},
)

def _impl(ctx):
    output = ctx.actions.declare_file(ctx.label.name + ".out")
    inputs = [ctx.file.src, ctx.file.alias] + ctx.files.extras + ctx.files.optional
    ctx.actions.run(
        executable = "/bin/sh",
        arguments = [
            "-c",
            "out=\"$1\"; printf 'message=%s\\ncontext=%s|%s|%s|%s\\n' \"$2\" \"$3\" \"$4\" \"$5\" \"$PHASE0_MESSAGE\" > \"$out\"; shift 5; for src in \"$@\"; do /bin/cat \"$src\" >> \"$out\" || exit; done",
            "phase0-action",
            output.path,
            ctx.attr.message + "|" + VERSION,
            MODULE_VALUE,
            ctx.toolchains["//:message_toolchain_type"].message,
            ctx.var["phase0_message"],
        ] + [f.path for f in inputs],
        inputs = inputs,
        outputs = [output],
        use_default_shell_env = True,
        mnemonic = "PhaseZeroFixture",
        progress_message = "Building deterministic phase-zero fixture",
    )
    return [DefaultInfo(files = depset([output]))]

fixture = rule(
    implementation = _impl,
    toolchains = ["//:message_toolchain_type"],
    attrs = {
        "src": attr.label(allow_single_file = True, mandatory = True),
        "alias": attr.label(allow_single_file = True, mandatory = True),
        "extras": attr.label_list(allow_files = True),
        "optional": attr.label_list(allow_files = True),
        "message": attr.string(mandatory = True),
    },
)
