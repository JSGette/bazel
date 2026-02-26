// Copyright 2025 The Bazel Authors. All rights reserved.
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
package com.google.devtools.build.lib.oteltracing;

import static com.google.common.truth.Truth.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.google.common.eventbus.EventBus;
import com.google.devtools.build.lib.events.Reporter;
import com.google.devtools.build.lib.runtime.CommandEnvironment;
import com.google.devtools.common.options.OptionsParser;
import com.google.devtools.common.options.OptionsParsingResult;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

/** Tests for {@link OTelTraceModule}. */
@RunWith(JUnit4.class)
public class OTelTraceModuleTest {

  @Test
  public void noOpWhenDisabled() {
    OTelTraceModule module = new OTelTraceModule();

    CommandEnvironment env = mockEnvironment();
    module.beforeCommand(env);

    assertThat(module.getMapper()).isNull();
    assertThat(module.getTracerProvider()).isNull();

    module.afterCommand();
  }

  @Test
  public void registersCommonCommandOptions() {
    OTelTraceModule module = new OTelTraceModule();
    assertThat(module.getCommonCommandOptions()).containsExactly(OTelTraceOptions.class);
  }

  @Test
  public void initializesWhenJsonFlagSet() throws Exception {
    OTelTraceModule module = new OTelTraceModule();

    CommandEnvironment env = mockEnvironment("--experimental_otel_trace_json=/tmp/trace.json");
    module.beforeCommand(env);

    assertThat(module.getMapper()).isNotNull();
    assertThat(module.getTracerProvider()).isNotNull();

    module.afterCommand();

    assertThat(module.getMapper()).isNull();
    assertThat(module.getTracerProvider()).isNull();
  }

  @Test
  public void afterCommand_cleansUpResources() throws Exception {
    OTelTraceModule module = new OTelTraceModule();

    CommandEnvironment env = mockEnvironment("--experimental_otel_trace_json=/tmp/trace.json");
    module.beforeCommand(env);
    assertThat(module.getTracerProvider()).isNotNull();

    module.afterCommand();

    assertThat(module.getTracerProvider()).isNull();
    assertThat(module.getMapper()).isNull();
  }

  private CommandEnvironment mockEnvironment(String... flags) {
    CommandEnvironment env = mock(CommandEnvironment.class);
    EventBus eventBus = new EventBus();
    when(env.getEventBus()).thenReturn(eventBus);
    when(env.getReporter()).thenReturn(new Reporter(eventBus));

    OptionsParser parser = OptionsParser.builder().optionsClasses(OTelTraceOptions.class).build();
    try {
      parser.parse(flags);
    } catch (Exception e) {
      throw new RuntimeException(e);
    }

    OptionsParsingResult result = mock(OptionsParsingResult.class);
    when(result.getOptions(OTelTraceOptions.class)).thenReturn(parser.getOptions(OTelTraceOptions.class));
    when(env.getOptions()).thenReturn(result);

    return env;
  }
}
