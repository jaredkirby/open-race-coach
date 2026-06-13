# Project CARS / Project CARS 2 Fixtures

## rest-cars_example.json

- Source: https://raw.githubusercontent.com/ocindev/rest-cars/master/rest-cars_example.json
- Repository: https://github.com/ocindev/rest-cars
- SHA-256: `21aa062b97653515c2ec68311f1c31c9fb8dc6d0e4a80f725e30df1f838c60fe`
- Size: 6,241 bytes

This fixture is public Project CARS 2 shared-memory-derived JSON exposed by the `rest-cars` tool, not a raw `$pcars2$` mmap byte dump. It is useful for checking real field names and values that overlap the AMS2/Project CARS 2 adapter, but it does not contain every field present in the binary shared-memory struct.

Known limitations:

- The JSON uses the upstream spellings `participiants` and `mParticipiantInfo`.
- The sample does not include `mCurrentLapDistance`, so tests must not claim it validates real lap-distance extraction.
- Live AMS2 validation still requires a Windows capture from the `$pcars2$` shared-memory region.

## crest_example.json

- Source: https://raw.githubusercontent.com/NLxAROSA/CREST/master/example.json.txt
- Repository: https://github.com/NLxAROSA/CREST
- SHA-256: `784cac9dd781c39e127cdc45102249ac1c04b153262cff657f1f3203c9143b54`
- Size: 13,705 bytes

This fixture is public Project CARS shared-memory-derived JSON exposed by CREST. It is closer to the full PC2 shared-memory shape than `rest-cars_example.json` for v0 adapter tests because it includes `mCurrentLapDistance`, participant arrays, car state, timing, event metadata, and track length.

Known limitations:

- The JSON records `mVersion: 5`, while the AMS2 adapter currently expects AMS2/PC2 shared-memory version 14 for live `$pcars2$` reads.
- The file is decoded JSON, not raw mmap bytes, so it cannot prove `ctypes` byte layout or live AMS2 field fidelity.
- Live AMS2 validation still requires a Windows capture from the `$pcars2$` shared-memory region.
