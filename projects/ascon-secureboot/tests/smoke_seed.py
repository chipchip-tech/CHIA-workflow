"""Seed the cache with a 'previous run' whose iter0 agent turn the loop replays.

  smoke_seed.py <cache_dir> <run_id>            scenario C: the turn added
      AsconConfigs.scala and patched Foo.scala; the loop must restore that tree
      into the fake Chipyard checkout, then build for real.
  smoke_seed.py <cache_dir> <run_id> violation  scenario E: the turn edited a
      file inside the rocket-chip submodule; the loop must report it.
"""
import io
import sys
import tarfile

import ray
from chia.base.ChiaFunction import get
from chia.base.cache import start_cache, stop_cache
from chia.models.opencode import OpenCodeQueryResult

cache_dir, run_id = sys.argv[1], sys.argv[2]
violation = len(sys.argv) > 3 and sys.argv[3] == "violation"
ray.init(address="auto")
stop_cache()
cache = start_cache(size=1, units="GB", cache_dir_path=cache_dir)

patch = """diff --git a/generators/chipyard/src/main/scala/Foo.scala b/generators/chipyard/src/main/scala/Foo.scala
--- a/generators/chipyard/src/main/scala/Foo.scala
+++ b/generators/chipyard/src/main/scala/Foo.scala
@@ -1 +1 @@
-// original
+// patched by the agent
"""
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tar:
    data = b"package chipyard\nclass AsconRocketConfig\n"
    info = tarfile.TarInfo("generators/chipyard/src/main/scala/config/AsconConfigs.scala")
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))
snap = {"error": "", "head": "x", "patch": patch, "full_diff": patch, "status": "",
        "untracked": ["generators/chipyard/src/main/scala/config/AsconConfigs.scala"],
        "skipped": [], "tar": buf.getvalue()}
reply = OpenCodeQueryResult(result="[seeded] added AsconConfigs.scala", returncode=0,
                            stderr="", stream_result="", success=True)
if violation:
    # Same shape as collect_tree() after an agent edited rocket-chip (STATUS_CMD
    # prefixes submodule lines with "<path>: ").
    empty = io.BytesIO()
    tarfile.open(fileobj=empty, mode="w:gz").close()
    snap = {"error": "", "head": "x", "patch": "", "full_diff": "",
            "status": "generators/rocket-chip:  M f.scala",
            "untracked": [], "skipped": [], "tar": empty.getvalue()}
    reply = OpenCodeQueryResult(result="[seeded] edited generators/rocket-chip/f.scala",
                                returncode=0, stderr="", stream_result="", success=True)
get(cache.write.remote(f"{run_id}/iter0/agent", reply))
get(cache.write.remote(f"{run_id}/iter0/collect", snap))
print("seeded:", get(cache.keys.remote()))
stop_cache()
