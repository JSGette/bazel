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

package com.google.devtools.build.lib.server;

import com.google.devtools.build.lib.server.CommandProtos.EngineIdentity;
import com.google.devtools.build.lib.server.CommandProtos.PingRequest;
import com.google.devtools.build.lib.server.CommandProtos.PingResponse;
import com.google.devtools.build.lib.server.CommandProtos.RunRequest;
import com.google.devtools.build.lib.server.CommandProtos.RunResponse;
import com.google.devtools.build.lib.server.CommandProtos.ServerInfo;
import com.google.devtools.build.lib.server.CommandServerGrpc.CommandServerBlockingStub;
import com.google.devtools.build.lib.server.FailureDetails.GrpcServer;
import com.google.protobuf.ByteString;
import io.grpc.ManagedChannel;
import io.grpc.ManagedChannelBuilder;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Iterator;
import java.util.concurrent.TimeUnit;

/** Sends requests directly to a live shared endpoint, bypassing launcher validation. */
public final class SharedServerProtocolProbe {
  public static void main(String[] args) throws Exception {
    ServerInfo info = ServerInfo.parseFrom(Files.readAllBytes(Path.of(args[0])));
    ManagedChannel channel =
        ManagedChannelBuilder.forTarget(info.getAddress()).usePlaintext().build();
    try {
      CommandServerBlockingStub stub = CommandServerGrpc.newBlockingStub(channel);
      PingRequest ping = PingRequest.newBuilder().setCookie(info.getRequestCookie()).build();
      PingResponse registration = stub.withDeadlineAfter(10, TimeUnit.SECONDS).ping(ping);
      if (!registration.getCookie().equals(info.getResponseCookie())
          || registration.getEngineInstanceId().isEmpty()) {
        throw new AssertionError("Shared endpoint did not authenticate a registration");
      }
      RunRequest request =
          RunRequest.newBuilder()
              .setCookie(info.getRequestCookie())
              .setClientDescription("shared protocol integration probe")
              .setEngineIdentity(
                  EngineIdentity.newBuilder()
                      .setWorkspaceDirectory(ByteString.copyFromUtf8(args[1]))
                      .setOutputBase(ByteString.copyFromUtf8(args[2])))
              .setEngineInstanceId(registration.getEngineInstanceId())
              .addArg(ByteString.copyFromUtf8("shutdown"))
              .build();
      reject(
          stub,
          info,
          request.toBuilder().clearEngineIdentity().build(),
          GrpcServer.Code.ENGINE_IDENTITY_MISMATCH);
      reject(
          stub,
          info,
          request.toBuilder().clearEngineInstanceId().build(),
          GrpcServer.Code.ENGINE_UNAVAILABLE);
      reject(
          stub,
          info,
          request.toBuilder().clearEngineIdentity().clearEngineInstanceId().build(),
          GrpcServer.Code.ENGINE_IDENTITY_MISMATCH);
      reject(
          stub,
          info,
          request.toBuilder().setEngineInstanceId("stale-registration").build(),
          GrpcServer.Code.ENGINE_UNAVAILABLE);
      for (boolean changeWorkspace : new boolean[] {true, false}) {
        EngineIdentity.Builder wrongIdentity = request.getEngineIdentity().toBuilder();
        if (changeWorkspace) {
          wrongIdentity.setWorkspaceDirectory(ByteString.copyFromUtf8(args[1] + "/other"));
        } else {
          wrongIdentity.setOutputBase(ByteString.copyFromUtf8(args[2] + "/other"));
        }
        reject(
            stub,
            info,
            request.toBuilder().setEngineIdentity(wrongIdentity).build(),
            GrpcServer.Code.ENGINE_IDENTITY_MISMATCH);
      }
      reject(
          stub,
          info,
          request.toBuilder()
              .clearArg()
              .addArg(ByteString.copyFromUtf8("clean"))
              .addArg(ByteString.copyFromUtf8("--expunge"))
              .build(),
          GrpcServer.Code.SHARED_SERVER_COMMAND_UNSUPPORTED);
      reject(
          stub,
          info,
          request.toBuilder().clearArg().build(),
          GrpcServer.Code.SHARED_SERVER_COMMAND_UNSUPPORTED);
      if (!stub.withDeadlineAfter(10, TimeUnit.SECONDS).ping(ping).equals(registration)) {
        throw new AssertionError("Rejected requests changed the engine registration");
      }
      System.out.println("8 raw Run requests rejected; engine registration unchanged");
    } finally {
      channel.shutdownNow();
      if (!channel.awaitTermination(10, TimeUnit.SECONDS)) {
        throw new AssertionError("Probe channel did not terminate");
      }
    }
  }

  private static void reject(
      CommandServerBlockingStub stub, ServerInfo info, RunRequest request, GrpcServer.Code code) {
    Iterator<RunResponse> responses = stub.withDeadlineAfter(10, TimeUnit.SECONDS).run(request);
    if (!responses.hasNext()) {
      throw new AssertionError("No rejection response");
    }
    RunResponse response = responses.next();
    if (!response.getCookie().equals(info.getResponseCookie())
        || !response.getFinished()
        || response.getExitCode() != 36
        || !response.getCommandId().isEmpty()
        || response.getTerminationExpected()
        || response.getFailureDetail().getGrpcServer().getCode() != code
        || responses.hasNext()) {
      throw new AssertionError("Request was not rejected before dispatch: " + code);
    }
  }

  private SharedServerProtocolProbe() {}
}
