"""Analysis-heavy DAG: scalar/string providers, string depsets, root-only writes."""

_MODULUS = 1000000007

NodeInfo = provider(fields = ["ids", "checksum", "payload", "payload_bytes", "children"])

def _name(partition, layer, column):
    return "n_p%s_l%s_c%s" % (partition, layer, column)

def _node_impl(ctx):
    code = (ctx.attr.partition + 1) * 1000003 + (ctx.attr.layer + 1) * 1009 + (ctx.attr.column + 1) * 97
    payload = []
    payload_bytes = 0
    checksum = code * 31
    for index in range(ctx.attr.payload_fields):
        text = "%s:f%s:%s" % (ctx.label.name, index, "x" * ctx.attr.payload_chars)
        payload.append(text)
        payload_bytes += len(text)
        checksum += (index + 1) * len(text)
    transitive = []
    children = []
    for index, dependency in enumerate(ctx.attr.deps):
        info = dependency[NodeInfo]
        transitive.append(info.ids)
        if ctx.attr.retain_provider_graph:
            children.append(info)
        checksum += (index + 1) * info.checksum
        # Consume every parent's payload, without flattening its transitive depset.
        for text in info.payload:
            checksum += len(text)
    return [
        NodeInfo(
            ids = depset(direct = [ctx.label.name], transitive = transitive),
            checksum = checksum % _MODULUS,
            payload = tuple(payload),
            payload_bytes = payload_bytes,
            children = tuple(children),
        ),
        DefaultInfo(),
    ]

_node = rule(
    implementation = _node_impl,
    attrs = {
        "deps": attr.label_list(providers = [NodeInfo]),
        "partition": attr.int(mandatory = True),
        "layer": attr.int(mandatory = True),
        "column": attr.int(mandatory = True),
        "payload_fields": attr.int(mandatory = True),
        "payload_chars": attr.int(mandatory = True),
        "retain_provider_graph": attr.bool(default = False),
    },
)

def _root_impl(ctx):
    transitive = []
    children = []
    checksum = 0
    payload_bytes = 0
    for index, dependency in enumerate(ctx.attr.deps):
        info = dependency[NodeInfo]
        transitive.append(info.ids)
        if ctx.attr.retain_provider_graph:
            children.append(info)
        checksum += (index + 1) * info.checksum
        payload_bytes += info.payload_bytes
    id_set = depset(transitive = transitive)
    ids = sorted(id_set.to_list())
    content = "partition=%s\nanalysis_nodes=%s\nlast_layer_inputs=%s\nchecksum=%s\nlast_layer_payload_bytes=%s\nids=%s\n" % (
        ctx.attr.partition,
        len(ids),
        len(ctx.attr.deps),
        checksum % _MODULUS,
        payload_bytes,
        ",".join(ids),
    )
    output = ctx.actions.declare_file(ctx.label.name + ".txt")
    ctx.actions.write(output, content)
    result = [DefaultInfo(files = depset([output]))]
    if ctx.attr.retain_provider_graph:
        result.append(NodeInfo(
            ids = id_set,
            checksum = checksum % _MODULUS,
            payload = (),
            payload_bytes = 0,
            children = tuple(children),
        ))
    return result

_root = rule(
    implementation = _root_impl,
    attrs = {
        "deps": attr.label_list(providers = [NodeInfo]),
        "partition": attr.int(mandatory = True),
        "retain_provider_graph": attr.bool(default = False),
    },
)

def analysis_dag(partitions, layers, width, fan_in, payload_fields, payload_chars, retain_provider_graph = False):
    """Generate disjoint partitions; each root demands its entire layered partition."""
    for partition in range(partitions):
        for layer in range(layers):
            for column in range(width):
                dependencies = []
                if layer > 0:
                    dependencies = [
                        ":" + _name(partition, layer - 1, (column + offset) % width)
                        for offset in range(fan_in)
                    ]
                _node(
                    name = _name(partition, layer, column),
                    deps = dependencies,
                    partition = partition,
                    layer = layer,
                    column = column,
                    payload_fields = payload_fields,
                    payload_chars = payload_chars,
                    retain_provider_graph = retain_provider_graph,
                )
        _root(
            name = "root_p%s" % partition,
            deps = [":" + _name(partition, layers - 1, column) for column in range(width)],
            partition = partition,
            retain_provider_graph = retain_provider_graph,
        )
