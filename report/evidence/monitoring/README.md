# FloodLab monitoring report evidence

The four scenarios were submitted through the real workbench form to Pi
using Responses. The workbench allocated a separate project for each
scenario and kept an earlier map linked to its answer after a follow-up.

| Case | Inputs and output | Record |
| --- | --- | --- |
| Horta Sud Sentinel-1 | sar/inputs: downloaded VV/VH pixels. sar/outputs: two masks, vectors, comparison rasters, maps, and PyQGIS checks. | runs/sar.json |
| Horta Sud roads | transport/outputs: three municipality/road scopes, PDF/QGIS, reviewed CSVs and independent length checks. | runs/transport.json |
| Kastelli | crete/inputs: downloaded EMS vector and provenance. crete/outputs: QGIS and measured overlap. | runs/crete.json |
| Putian–Xianyou | putian/outputs: the successful catalog queries and image footprints, no raster download. | runs/putian.json |

The raw traces identify the original local workspace, which may not exist
on another machine. The archived outputs keep paths relative to each
case root. Extract a SAR project archive without flattening inputs and
outputs; open the QGZ under outputs with the source TIFFs still in inputs.
Candidate extent and asynchronous agreement are not survey or flood
classification accuracy.

## Re-run through the app

Start workbench/server.py against the bundled QGIS runtime, choose a
Responses model in the interface or use the hidden key prompt, and submit
the prompts from the trace. workbench/check_monitoring.mjs runs the same
prompts through the browser and verifies artifact display/download.
event/verify_sar.py and event/verify_transport.py independently open
the saved files and verify area and length consistency, respectively.
