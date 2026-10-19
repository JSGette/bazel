# Package resolution: Bazel 8.5 vs 9 (case-sensitivity / readdir)

This doc summarizes differences in package resolution and directory listing between 8.5.x and 9.x, in the context of the "Failed to list directory contents, for …/build, skipping: No such file or directory" error that can occur on case-insensitive hosts (e.g. macOS) with bind mounts into a case-sensitive context (e.g. Linux in Docker).

## Flow (same in 8.5 and 9)

- **Recursive package traversal:** `ProcessPackageDirectory` requests `DirectoryListingValue` for a directory, then for each `Dirent` with type directory/symlink it recurses by requesting the same for `rootRelativePath.getRelative(dirent.getName())`. So the **child path is always built from `dirent.getName()`** — i.e. whatever name the directory listing returned (from the syscall cache / `readdir`).
- **Error site:** If `readdir(path)` fails (e.g. `ENOENT` because the path’s casing doesn’t match the on-disk name), the error is reported in `ProcessPackageDirectory.reportErrorAndReturn("Failed to list directory contents", …)`.

So the failure can only occur when:

1. We have a directory whose on-disk name differs in casing from what we use (e.g. on-disk `build` vs path `…/BUILD`).
2. We call `readdir` on a path built from that name (either from the parent listing’s `dirent.getName()`, or from another code path that constructed the path with different casing).

## Differences between 8.5 and 9

### 1. `DefaultSyscallCache` and `Dirents`

- **9:** Uses `CompactSortedDirents` (implements `Dirents`). Directory listings are stored as sorted dirents with `maybeGetDirent(baseName)` (exact string match).
- **8.5:** Uses a raw `Collection<Dirent>`; no `maybeGetDirent` API.

### 2. `DefaultSyscallCache.getType(Path, Symlinks)` (9 only)

In 9, when the cache has a parent listing but **no exact match** for `path.getBaseName()`:

- If the filesystem is **not** `mayBeCaseOrNormalizationInsensitive()`, returns `null` (caller does stat).
- If it **is** potentially case-insensitive and the base name is ASCII, 9 checks whether any dirent matches **case-insensitively**. If so, it **falls back to `stat(path)`** instead of failing. So `getType("libs/BUILD")` can succeed via stat even when the listing only has `"build"`.

This only affects code paths that call `getType()` with a path whose base name might have wrong casing (e.g. glob or other lookups by name). It does **not** change the recursion path: `ProcessPackageDirectory` still uses `dirent.getName()` from the parent listing to build the child path, and the **child’s** `DirectoryListingValue` still requires a successful `readdir(childPath)`.

### 3. `DirectoryListingValue` key

- **9:** Uses a separate `DirectoryListingKey` type.
- **8.5:** Uses an inner `Key` type.

Behavior for “request listing for this path” is the same; key structure differs.

### 4. Native `readdir`

- **9:** “Simplify NativePosixFiles.readdir()” (commit `0d4bd0d253`): return type and follow-up stat behavior changed; directory entry **names** still come from `entry->d_name` in the JNI code. So the **casing of names** is unchanged by this commit.

### 5. `IgnoredSubdirectoriesFunction` / `.bazelignore`

- Only minor diffs (imports, `success.getPath()` vs `success.root()`). No change in how `build`-like directories are ignored.

## Why the error might not reproduce on 9

There is **no single change in 9** that clearly “fixes” the readdir ENOENT on a path like `…/build`:

1. **Recursion path is unchanged:** Child path is still `rootRelativePath.getRelative(dirent.getName())`. So if the parent listing (from `readdir`) returns the wrong casing (e.g. `BUILD` instead of `build`), we still request `readdir("…/BUILD")` and can still get ENOENT in 9.
2. **`getType()` fallback:** In 9, `getType(path)` can succeed via stat when the listing has no exact match but a case-insensitive match. That avoids failures in code paths that only need the **type** of a path (e.g. some glob or inference logic). It does **not** remove the need to call `readdir(childPath)` when recursing; it only helps when the failing call is a type check, not a full listing.
3. **Environment / timing:** Different evaluation order, cache state, or Docker/kernel behavior could make the kernel return the **actual** on-disk name in 9 runs (e.g. `build`), so the path we build is correct and we never hit ENOENT.

So the most plausible explanations for “cannot reproduce on 9” are:

- **Different environment or cache order** so that the path requested for listing always matches the on-disk name (e.g. kernel always returns `build`).
- **Some code path in 8.5** that requests a directory listing for a path built with wrong casing (e.g. from a BUILD file or glob) is in 9 either avoided or “rescued” by the `getType()` stat fallback so we never request that listing with wrong casing. That would require a code path that both (a) builds a path with wrong casing and (b) triggers a **listing** (not just a type check); such a path has not been clearly identified in the 8.5 vs 9 diff.

## Recommendation

The **readdir retry in `DefaultSyscallCache.readdirImpl()`** (on ENOENT, when the base name is build-like, retry using the actual directory name from the parent listing) is the right fix: it addresses the root cause regardless of which code path built the path with wrong casing, and it does not hide real errors (if no matching directory exists in the parent, the original exception is rethrown). It is independent of 8.5 vs 9 and remains valid for 9 if the same environment ever produces the wrong-casing path there.
