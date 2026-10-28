"""Deterministic analysis + output fixture without a shell or external toolchain."""

def _write_impl(ctx):
    output = ctx.actions.declare_file("fixture.txt")
    ctx.actions.write(output, "skycache paired fixture\n")
    return [DefaultInfo(files = depset([output]))]

write_fixture = rule(implementation = _write_impl)
