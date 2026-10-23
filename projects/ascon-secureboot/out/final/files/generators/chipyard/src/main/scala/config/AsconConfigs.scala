package chipyard

import org.chipsalliance.cde.config.Config

class AsconRocketConfig extends Config(
  new chipyard.config.WithAsconRoCC ++
  new freechips.rocketchip.rocket.WithNHugeCores(1) ++
  new chipyard.config.AbstractConfig)
