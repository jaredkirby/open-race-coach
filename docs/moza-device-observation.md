# MOZA device observation boundary

Status: pinned SDK documentation/metadata inspection and a private offline
response interpreter. Confidence is high in the inspected signatures and enum
mapping; compatibility, SDK initialization effects and actual device reads remain
unverified. No native SDK backend is deployed or executed.

The public [MOZA Racing SDK download](https://mozaracing.com/pages/sdk) inspected
here contains the native `1.0.1.8` tree and managed wrappers. Its documentation
requires SDK-compatible Pit House and an `installMozaSDK()` initialization call
before general SDK functions. The docs describe that function as loading the SDK;
its name alone does not mean a Windows installer. The inspected material does not
establish its complete side effects or non-interference with existing driving tools.

The candidate device query is explicitly `getDeviceParent`, despite its name. The
header describes a device-name return, with an empty value indicating no connection.
The managed signature returns a string and takes `PRODUCTTYPE` plus a **ref**
`ERRORCODE`. The first proposed read set is wheelbase, steering wheel and pedals
(product values 0, 1 and 3). It does not use HID readers, setters, calibration,
force-effect creation, motor movement or driver remapping.

| Query evidence | Interpretation |
| --- | --- |
| `NORMAL` and a valid nonempty name | Reported connection; compare the exact name with the reviewed expected model. |
| `NORMAL` and an empty string | Documented disconnected case. |
| Null/malformed name or wrong product | Unknown; do not manufacture a connected or disconnected device. |
| Any nonzero/unknown error | Preserve the SDK error and keep connection unknown. Ignore any accompanying old name. |
| Connected name without an approved model | Connection information only; model readiness is unresolved. |

In particular, SDK initialization, Pit House readiness and firmware errors must
not become instructions to recalibrate or update hardware automatically. The
private interpreter checks these distinctions against reflected metadata without
loading vendor code for execution. Passing it proves interpretation of supplied
responses, not device connection, calibration, correct profile or physical controls.

## Qualification before a native reader

Pin the managed wrapper and matching native architecture/dependencies. The package
notes that AnyCPU applies only to the managed DLL; it does not remove the need for
matching native components. Version strings and a running Device Service alone
cannot establish SDK compatibility. The installed driver-control interface is a
different API and must not be substituted for the SDK device query.

Treat initialization and cleanup as a separately reviewed lifecycle. Establish
their app/service/device effects and timeout/recovery behavior before using them
on the rig. A future helper needs process-lifetime/source binding, bounded calls,
fresh coherent observations and retained failure evidence. Do not invoke vendor
examples as a readiness test: they include calibration and force-feedback code.
No firmware change, SDK-mode change or application replacement follows from this
inspection.

This route is optional. Existing scoped human or supported UI device evidence
can satisfy readiness; an unavailable SDK is an observer limitation, not proof of
a broken rig. Preserve accepted calibration and normal driving evidence. A future
SAS tool should expose only the bounded typed read/result contract; thread identity
must not confer initialization or hardware-control authority. See
[rig observation interfaces](rig-observation-interfaces.md) and
[Chief readiness assessment](chief-readiness-assessment.md).
