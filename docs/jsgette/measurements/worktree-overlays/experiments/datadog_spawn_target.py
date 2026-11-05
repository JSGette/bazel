"""Datadog's eBPF header-preprocessor generator as an external-spawn workload."""

from functools import partial

import datadog_stress_target as fixture


TARGET = fixture.SPAWN_TARGET
REVISION = fixture.REVISION
BUILD_FLAGS = fixture.BUILD_FLAGS
VARIANTS = fixture.VARIANTS
materialize = partial(fixture.materialize, target=TARGET)
apply_variant = partial(fixture.apply_variant, target=TARGET)
expected_output = partial(fixture.expected_output, target=TARGET)
artifact = partial(fixture.artifact, target=TARGET)
