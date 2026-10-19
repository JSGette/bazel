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

#[cfg(unix)]
fn main() -> std::io::Result<()> {
    use std::os::unix::net::UnixListener;
    use std::time::Duration;

    let args: Vec<_> = std::env::args_os().collect();
    if !(2..=4).contains(&args.len()) || (args.len() == 4 && args[3] != "--watch-parent-stdin") {
        eprintln!("usage: skystore SOCKET_PATH [NEW_SPILL_FILE [--watch-parent-stdin]]");
        std::process::exit(2);
    }
    if args.len() == 4 {
        // The parent keeps this pipe open. EOF also terminates the sidecar if the JVM crashes.
        std::thread::spawn(|| {
            use std::io::Read;
            let mut byte = [0];
            let _ = std::io::stdin().read(&mut byte);
            std::process::exit(0);
        });
    }
    // Require a caller-owned private directory. Never unlink an existing path:
    // it may be another sidecar's live socket or an unrelated file.
    let listener = UnixListener::bind(&args[1])?;
    let mut store = if args.len() >= 3 {
        skystore::Store::disk(std::path::Path::new(&args[2]))?
    } else {
        skystore::Store::default()
    };
    for connection in listener.incoming() {
        let mut stream = connection?;
        stream.set_read_timeout(Some(Duration::from_secs(30)))?;
        stream.set_write_timeout(Some(Duration::from_secs(30)))?;
        if let Err(error) = skystore::serve(&mut stream, &mut store) {
            eprintln!("skystore: closing client: {error}");
        }
    }
    Ok(())
}

#[cfg(not(unix))]
fn main() {
    eprintln!("the Phase 0 skystore prototype requires Unix sockets on Linux or macOS");
    std::process::exit(2);
}
