# Karla Lake recovery monitoring: three-turn evidence

This is the main case in the course report. All three successful turns were
submitted through the browser workbench to Pi. Both acquisition calls
returned live_remote_window. The inputs are study-window pixels, not whole
satellite scenes. The third turn reused the existing comparison.

| Output | Content |
| --- | --- |
| sar-mask-3497eeb209 | Radar observations and initial candidate mask |
| optical-mask-244ef38415 | Optical observations and candidate mask |
| comparison-ce8daf32d0 | Joint-coverage evidence and same-date EMSN184 comparison |
| exposure-c6e0b9df2e | Study-window cropland and road screening |
| exposure-4fa28e86a4 | Northern focus window, reusing the existing comparison |

Open map.qgz in a case folder. Keep the case structure when extracting an
individual project.zip. karla.json records tool inputs, method reasoning and
outputs. data-index.json ties artifacts to the workspace. verification.json
contains independent area, road-length, pixel-class and project checks.
Reference product data and the road snapshot can be found in sources/.

The report's three-turn diagram and call-argument table are derived from
karla.json. The two printed trace panels are viewport crops of the
actual finished second and third replies, captured by
report/capture_karla_trace.mjs without altering their text or results.
The stacked bars are rendered by report/plot_karla_review.py from the
two exposure results.json files; the scopes are nested, not independent.

The road snapshot was retrieved on 2026-11-25; it is not a 2023 historical
network. Cropland is ESA WorldCover 2021 class 40. The radar/optical comparison
does not measure flood expansion or recession. The official comparison uses
overlapping scenes and is not independent ground truth.

To repeat through a local workbench with a configured Responses provider:

~~~sh
FLOODLAB_PREVIEW=http://127.0.0.1:8781 node workbench/check_monitoring.mjs /tmp/karla-run karla
~~~

Use event/verify_karla.py in the bundled QGIS runtime to verify an extracted
workspace. No service key is needed to open the finished maps.
