# Bundled Components

FloodLab includes the complete QGIS conda-forge runtime and a local Python agent.
It includes Pi Agent Core 1.6.0 and Pi AI's Responses adapter (MIT), bundled in
`event/pi-worker.cjs`. Source: https://github.com/earendil-works/pi .
The app uses Electron's Node runtime; no separate Pi or Codex installation is required.
Language-model inference uses the API endpoint configured by the user.

- QGIS 3.44.14: GPL-2.0-or-later. Source:
  https://github.com/qgis/QGIS/tree/final-3_44_14
  Build recipes and patches: https://github.com/conda-forge/qgis-feedstock
- Conda-forge packages: exact download URLs, versions, licenses and SHA-256 hashes
  are recorded in runtime-bundle/runtime.json. Original package archives, including
  their `info/licenses` and recipe metadata where provided, remain in the bundle.
  Sources and patches for each package are described by those build recipes.
- Micromamba 2.10.0: BSD-3-Clause. Source and bundled dependency notices:
  https://github.com/mamba-org/mamba
- Electron: MIT; Chromium and other component notices are supplied in the Electron
  distribution (`LICENSE` and `LICENSES.chromium.html`).
- Leaflet 1.9.4: BSD-2-Clause; license included next to the frontend dependency.
- Lucide 0.468.0: ISC; license included next to the frontend dependency.
- OpenStreetMap historical roads: ODbL; attribution and provenance are included
  with the sample data.
- Copernicus EMSR773 and Sentinel-2: source attribution is retained in the sample
  data, application and exported maps.

The desktop source, Python agent and build scripts accompany the course delivery.
This is an unsigned course-project distribution, not an official QGIS product.
Public redistribution should include corresponding source and license obligations
for all included packages; this document is an inventory, not legal certification.
