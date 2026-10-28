"""Toolchain-free action registration and overlapping-root fixture."""

def _writer_impl(ctx):
    outputs = []
    for path in ctx.attr.paths:
        output = ctx.actions.declare_file(path)
        ctx.actions.write(
            output = output,
            content = "skycache_action_fanout_v1\noutput=%s\ntag=%s\n" % (path, ctx.attr.content_tag),
            is_executable = ctx.attr.executable,
        )
        outputs.append(output)
    return [DefaultInfo(files = depset(outputs))]

action_writer = rule(
    implementation = _writer_impl,
    attrs = {
        "paths": attr.string_list(mandatory = True),
        "content_tag": attr.string(default = "fixture"),
        "executable": attr.bool(default = False),
    },
)

def _root_impl(ctx):
    return [DefaultInfo(files = depset(transitive = [dep[DefaultInfo].files for dep in ctx.attr.deps]))]

action_root = rule(
    implementation = _root_impl,
    attrs = {"deps": attr.label_list(providers = [DefaultInfo])},
)

def action_fanout(partitions, writers_per_partition, outputs_per_writer):
    for partition in range(partitions):
        writers = []
        for writer in range(writers_per_partition):
            name = "writer_p%d_w%d" % (partition, writer)
            action_writer(
                name = name,
                paths = ["%s_o%d.txt" % (name, output) for output in range(outputs_per_writer)],
            )
            writers.append(":" + name)
        action_root(name = "actions_p%d" % partition, deps = writers)
