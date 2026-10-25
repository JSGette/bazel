def _copy(ctx):
    out = ctx.actions.declare_file(ctx.label.name + ".txt")
    ctx.actions.run_shell(inputs = [ctx.file.src], outputs = [out],
        command = 'cat "$1" > "$2"; echo "$3" >> "$2"',
        arguments = [ctx.file.src.path, out.path, ctx.label.name],
        execution_requirements = {"no-remote": "1"} if ctx.attr.local else {})
    return [DefaultInfo(files = depset([out]))]
copy = rule(implementation = _copy, attrs = {
    "src": attr.label(allow_single_file = True), "local": attr.bool()})
def _write(ctx):
    out = ctx.actions.declare_file(ctx.label.name + ".txt")
    ctx.actions.write(out, "internal-write\n")
    return [DefaultInfo(files = depset([out]))]
write = rule(implementation = _write)
