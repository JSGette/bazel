"""Evict only experiment files; verify Linux page-cache residency without faulting them in."""
import ctypes, os
from pathlib import Path

libc=ctypes.CDLL(None,use_errno=True)
libc.mmap.restype=ctypes.c_void_p
libc.mmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_long]
libc.mincore.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p]
libc.munmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
PAGE=os.sysconf('SC_PAGE_SIZE')

def residency(fd,length):
 if not length:return (0,0)
 address=libc.mmap(None,length,1,1,fd,0) # PROT_READ, MAP_SHARED; no access to mapped bytes.
 if address==ctypes.c_void_p(-1).value:raise OSError(ctypes.get_errno(),'mmap')
 try:
  pages=(length+PAGE-1)//PAGE;vec=(ctypes.c_ubyte*pages)()
  if libc.mincore(address,length,vec):raise OSError(ctypes.get_errno(),'mincore')
  return pages,sum(x&1 for x in vec)
 finally:libc.munmap(address,length)

def evict(directory):
 directory=Path(directory).resolve(strict=True)
 assert '/.work/skycache-retention-2026-10-27/' in str(directory),directory
 files=pages=before=after=total=0
 for path in sorted(directory.rglob('*')):
  if path.is_symlink() or not path.is_file():continue
  fd=os.open(path,os.O_RDONLY)
  try:
   size=os.fstat(fd).st_size;total+=size
   count,resident=residency(fd,size);pages+=count;before+=resident
   os.fsync(fd)
   os.posix_fadvise(fd,0,0,os.POSIX_FADV_DONTNEED)
   after+=residency(fd,size)[1];files+=1
  finally:os.close(fd)
 return dict(files=files,bytes=total,pages=pages,resident_before=before,resident_after=after,
             resident_fraction_after=after/pages if pages else 0)
