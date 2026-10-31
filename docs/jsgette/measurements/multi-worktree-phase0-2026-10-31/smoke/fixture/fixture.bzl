VERSION = "base-bzl"

def _impl(ctx):
    output = ctx.actions.declare_file(ctx.label.name + ".out")
    inputs = [ctx.file.src, ctx.file.alias] + ctx.files.extras + ctx.files.optional
    ctx.actions.run(
        executable = "/bin/sh",
        arguments = [
            "-c",
            "out=\"$1\"; printf 'message=%s\\n' \"$2\" > \"$out\"; shift 2; for src in \"$@\"; do /bin/cat \"$src\" >> \"$out\" || exit; done",
            "phase0-action",
            output.path,
            ctx.attr.message + "|" + VERSION,
        ] + [f.path for f in inputs],
        inputs = inputs,
        outputs = [output],
        mnemonic = "PhaseZeroFixture",
        progress_message = "Building deterministic phase-zero fixture",
    )
    return [DefaultInfo(files = depset([output]))]

fixture = rule(
    implementation = _impl,
    attrs = {
        "src": attr.label(allow_single_file = True, mandatory = True),
        "alias": attr.label(allow_single_file = True, mandatory = True),
        "extras": attr.label_list(allow_files = True),
        "optional": attr.label_list(allow_files = True),
        "message": attr.string(mandatory = True),
    },
)
