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

//! An ephemeral batch value store for measuring the Java/Rust IPC boundary.
//!
//! This is not a persistent Skyframe graph. Keys are opaque 16-byte fingerprints;
//! the caller owns serialization, fingerprinting, and validity.

use std::collections::HashMap;
use std::io::{self, Read, Write};

pub const VERSION: u8 = 1;
pub const PING: u8 = 0;
pub const PUT: u8 = 1;
pub const GET: u8 = 2;
pub const MAX_FRAME: usize = 16 * 1024 * 1024;
pub const MAX_BATCH: usize = 4096;
pub const MAX_STORE: usize = 64 * 1024 * 1024;
pub type Key = [u8; 16];

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

/// Reads one frame, distinguishing a clean disconnect from a truncated frame.
pub fn read_frame(reader: &mut impl Read) -> io::Result<Option<Vec<u8>>> {
    let mut header = [0; 4];
    loop {
        match reader.read(&mut header[..1]) {
            Ok(0) => return Ok(None),
            Ok(_) => break,
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e),
        }
    }
    reader.read_exact(&mut header[1..])?;
    let size = u32::from_be_bytes(header) as usize;
    if !(2..=MAX_FRAME).contains(&size) {
        return Err(invalid("frame length outside bounds"));
    }
    let mut frame = vec![0; size];
    reader.read_exact(&mut frame)?;
    Ok(Some(frame))
}

pub fn write_frame(writer: &mut impl Write, frame: &[u8]) -> io::Result<()> {
    if !(2..=MAX_FRAME).contains(&frame.len()) {
        return Err(invalid("frame length outside bounds"));
    }
    writer.write_all(&(frame.len() as u32).to_be_bytes())?;
    writer.write_all(frame)?;
    writer.flush()
}

struct Decoder<'a> {
    remaining: &'a [u8],
}

impl<'a> Decoder<'a> {
    fn take(&mut self, size: usize) -> io::Result<&'a [u8]> {
        if size > self.remaining.len() {
            return Err(invalid("truncated request"));
        }
        let (value, rest) = self.remaining.split_at(size);
        self.remaining = rest;
        Ok(value)
    }

    fn number(&mut self) -> io::Result<usize> {
        Ok(u32::from_be_bytes(self.take(4)?.try_into().unwrap()) as usize)
    }

    fn key(&mut self) -> io::Result<Key> {
        Ok(self.take(16)?.try_into().unwrap())
    }

    fn count(&mut self) -> io::Result<usize> {
        let count = self.number()?;
        if count > MAX_BATCH {
            return Err(invalid("batch too large"));
        }
        Ok(count)
    }

    fn finish(self) -> io::Result<()> {
        if self.remaining.is_empty() {
            Ok(())
        } else {
            Err(invalid("trailing request bytes"))
        }
    }
}

/// Bounded, process-lifetime storage. No eviction or reuse after restart.
#[derive(Default)]
pub struct Store {
    values: HashMap<Key, Vec<u8>>,
    bytes: usize,
}

impl Store {
    pub fn handle(&mut self, frame: &[u8]) -> io::Result<Vec<u8>> {
        if !(2..=MAX_FRAME).contains(&frame.len()) {
            return Err(invalid("frame length outside bounds"));
        }
        let mut input = Decoder { remaining: frame };
        if input.take(1)?[0] != VERSION {
            return Err(invalid("unsupported protocol version"));
        }
        let operation = input.take(1)?[0];
        let mut output = vec![VERSION, operation];
        match operation {
            PING => input.finish()?,
            PUT => {
                let count = input.count()?;
                // Parse and validate the entire batch before changing any state.
                // Duplicate keys use the final value, preserving request order semantics.
                let mut updates = HashMap::new();
                for _ in 0..count {
                    let key = input.key()?;
                    let size = input.number()?;
                    updates.insert(key, input.take(size)?);
                }
                input.finish()?;
                let mut new_size = self.bytes;
                for (key, value) in &updates {
                    new_size -= self.values.get(key).map_or(0, |old| 16 + old.len());
                    new_size += 16 + value.len();
                }
                if new_size > MAX_STORE {
                    return Err(invalid("store budget exceeded"));
                }
                for (key, value) in updates {
                    self.values.insert(key, value.to_vec());
                }
                self.bytes = new_size;
                output.extend_from_slice(&(count as u32).to_be_bytes());
            }
            GET => {
                let count = input.count()?;
                let mut keys = Vec::with_capacity(count);
                let mut response_size = 6;
                for _ in 0..count {
                    let key = input.key()?;
                    response_size += 1 + self.values.get(&key).map_or(0, |v| 4 + v.len());
                    if response_size > MAX_FRAME {
                        return Err(invalid("response too large; reduce batch size"));
                    }
                    keys.push(key);
                }
                input.finish()?;
                output.reserve(response_size - 2);
                output.extend_from_slice(&(count as u32).to_be_bytes());
                for key in keys {
                    match self.values.get(&key) {
                        None => output.push(0),
                        Some(value) => {
                            output.push(1);
                            output.extend_from_slice(&(value.len() as u32).to_be_bytes());
                            output.extend_from_slice(value);
                        }
                    }
                }
            }
            _ => return Err(invalid("unknown operation")),
        }
        Ok(output)
    }
}

/// A malformed request terminates this connection; later clients may reconnect.
pub fn serve(stream: &mut (impl Read + Write), store: &mut Store) -> io::Result<()> {
    while let Some(request) = read_frame(stream)? {
        write_frame(stream, &store.handle(&request)?)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn put(entries: &[(Key, &[u8])]) -> Vec<u8> {
        let mut frame = vec![VERSION, PUT];
        frame.extend_from_slice(&(entries.len() as u32).to_be_bytes());
        for (key, value) in entries {
            frame.extend_from_slice(key);
            frame.extend_from_slice(&(value.len() as u32).to_be_bytes());
            frame.extend_from_slice(value);
        }
        frame
    }

    fn get(keys: &[Key]) -> Vec<u8> {
        let mut frame = vec![VERSION, GET];
        frame.extend_from_slice(&(keys.len() as u32).to_be_bytes());
        for key in keys {
            frame.extend_from_slice(key);
        }
        frame
    }

    #[test]
    fn missing_empty_and_ordered_duplicate_reads_are_distinct() {
        let mut store = Store::default();
        store
            .handle(&put(&[([1; 16], b"abc"), ([2; 16], b"")]))
            .unwrap();
        assert_eq!(
            store
                .handle(&get(&[[2; 16], [3; 16], [1; 16], [1; 16]]))
                .unwrap(),
            [
                1, GET, 0, 0, 0, 4, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 3, 97, 98, 99, 1, 0, 0, 0, 3, 97,
                98, 99
            ]
        );
    }

    #[test]
    fn duplicate_puts_replace_and_account_only_final_value() {
        let mut store = Store::default();
        store
            .handle(&put(&[([1; 16], b"abc"), ([1; 16], b"xy")]))
            .unwrap();
        assert_eq!(store.bytes, 18);
        store.handle(&put(&[([1; 16], b"")])).unwrap();
        assert_eq!(store.bytes, 16);
    }

    #[test]
    fn malformed_batch_cannot_partially_modify_store() {
        let mut store = Store::default();
        let request = put(&[([1; 16], b"abc"), ([2; 16], b"def")]);
        for end in 0..request.len() {
            assert!(store.handle(&request[..end]).is_err());
            assert!(store.values.is_empty());
        }
        let mut trailing = request.clone();
        trailing.push(0);
        assert!(store.handle(&trailing).is_err());
        assert!(store.values.is_empty());
        store.handle(&request).unwrap();
        assert_eq!(store.values.len(), 2);
    }

    #[test]
    fn rejects_unknown_version_operation_and_excessive_count() {
        let mut store = Store::default();
        for request in [
            &[2, PING][..],
            &[1, 255],
            &[1, PING, 0],
            &[1, GET, 0, 0, 16, 1],
            &[1, PUT, 255, 255, 255, 255],
        ] {
            assert!(store.handle(request).is_err());
        }
        assert_eq!(store.handle(&[VERSION, PING]).unwrap(), [VERSION, PING]);
        assert_eq!(store.handle(&get(&[])).unwrap(), [VERSION, GET, 0, 0, 0, 0]);
        assert_eq!(store.handle(&put(&[])).unwrap(), [VERSION, PUT, 0, 0, 0, 0]);
    }

    #[test]
    fn rejects_oversized_response_even_when_request_is_small() {
        let mut store = Store::default();
        store
            .handle(&put(&[([1; 16], &vec![0; MAX_FRAME / 2])]))
            .unwrap();
        assert!(store.handle(&get(&[[1; 16], [1; 16]])).is_err());
        assert!(store.handle(&get(&[[1; 16]])).is_ok());
    }

    #[test]
    fn store_limit_rejects_whole_batch_but_allows_replacements() {
        let mut store = Store::default();
        let value = vec![0; MAX_FRAME / 2];
        for id in 0..7 {
            store.handle(&put(&[([id; 16], &value)])).unwrap();
        }
        let before = store.bytes;
        assert!(store.handle(&put(&[([8; 16], &value)])).is_err());
        assert_eq!(store.bytes, before);
        assert!(!store.values.contains_key(&[8; 16]));
        store.handle(&put(&[([0; 16], b"x")])).unwrap();
        store.handle(&put(&[([8; 16], &value)])).unwrap();
    }

    #[test]
    fn frame_eof_truncation_and_size_limits() {
        assert!(read_frame(&mut Cursor::new([])).unwrap().is_none());
        for bytes in [
            &[0][..],
            &[0, 0, 0],
            &[0, 0, 0, 2, VERSION],
            &[0, 0, 0, 0],
            &[255, 255, 255, 255],
        ] {
            assert!(read_frame(&mut Cursor::new(bytes)).is_err());
        }
        let mut wire = Vec::new();
        write_frame(&mut wire, &[VERSION, PING]).unwrap();
        assert_eq!(wire, [0, 0, 0, 2, VERSION, PING]);
        assert_eq!(
            read_frame(&mut Cursor::new(wire)).unwrap(),
            Some(vec![VERSION, PING])
        );
    }

    #[cfg(unix)]
    #[test]
    fn socket_serves_multiple_requests_and_fragmented_frames() {
        use std::os::unix::net::UnixStream;
        let (mut client, mut server) = UnixStream::pair().unwrap();
        let worker = std::thread::spawn(move || serve(&mut server, &mut Store::default()));
        for _ in 0..2 {
            for byte in [0, 0, 0, 2, VERSION, PING] {
                client.write_all(&[byte]).unwrap();
            }
            assert_eq!(read_frame(&mut client).unwrap(), Some(vec![VERSION, PING]));
        }
        drop(client);
        worker.join().unwrap().unwrap();
    }
}
