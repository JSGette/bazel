package com.google.devtools.build.lib.skyframe.serialization;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;

/** Compare the monolithic binaries' complete dynamic class inventories and registry checksums. */
public final class ChannelRegistryCheck {
  public static void main(String[] args) throws Exception {
    var registry = AutoRegistry.get();
    var names = registry.classNames();
    System.out.println("classes=" + names.size());
    System.out.println("classes_sha256=" + HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
        .digest(String.join("\n", names).getBytes(StandardCharsets.UTF_8))));
    System.out.println("registry_sha256=" + HexFormat.of().formatHex(registry.getBuilder()
        .computeChecksum(true).build().getChecksum()));
  }
}
