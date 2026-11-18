# Synthetic input gateway

Schema version: 1. Owner: `simcoach.live.input_gateway`.
Write timing: owner/binding events commit before issuing a credential; intake intent
commits before calling the coordinator. Command receipts remain authoritative.
Compatibility: additive events in the existing input journal. No installed service,
real UI backend, saved credential migration or automatic startup is introduced.

## Authority and lifetime

One gateway per live coordinator object; the coordinator's native Windows-session
claim remains the resource owner. Gateway epoch is a fresh UUID on construction,
bound to the actual host PID and native creation time. Restart restores neither
credentials nor preparation permission. Queued and uncertain command recovery
continues to follow [input_coordinator.md](input_coordinator.md).

The trusted in-process Chief supplies one principal label and 1–64 exact commands
for the active generation. Serialized commands are capped at65536 UTF-8 bytes and
copied before binding. IDs must be distinct and generations match. Full command
validation still occurs in coordinator admission. A principal is an asserted host
identity; possession of the bearer is the credential, not proof of an OS account,
SAS relationship or independent process identity. This is a local trust boundary,
not protection against a hostile process under the same user account.

Only one binding is issued per generation. New explicit preparation may bind a new
credential; an old caller cannot cancel that newer generation. Cancellation checks
the generation under the same owner lock as revocation, including after earlier
authentication. Commands cannot be altered through this interface.

The trusted in-process Chief can extend the same binding after every prior command
is verified or skipped. Extension snapshots new commands, refuses existing IDs,
retains the same credential/generation and enforces the same total64-command/65536
byte bound. Pending, cancelled, uncertain or replaced authority refuses extension.
There is no HTTP extension operation. Recipe sequencing/fresh provenance checks
belong to [session_recipe.md](session_recipe.md); extension itself is not review.

The returned private descriptor has required schema_version(integer1), owner(UUID
epoch), process({pid:positive integer, created_100ns:decimal string}), binding(UUID),
principal(label), generation(UUID), token(64 random lowercase hex characters).
The token and command copies are memory-only. Do not publish descriptors or log
credentials. Native creation-time unit is100ns Windows FILETIME; command deadlines
keep the coordinator's performance-counter seconds. Principal labels follow the
coordinator's bounded label rules.

## Requests and responses

Required envelope: schema_version(integer1), owner, binding, principal and op.
op enum: submit, inspect, cancel. Submit/inspect additionally require command_id;
cancel forbids it. No extra fields are accepted. There is no remote grant, bind,
dispatch, reconciliation, shell, file, model or device-control operation.

Responses include schema_version, owner, process, binding and principal. Clients
must compare those to their trusted descriptor. Submit/inspect return receipt,
nullable until admission, containing only id, fingerprint, state and nullable
reason from the coordinator. Duplicate intake returns the existing receipt. A
receipt is distinct from model completion and is not physical input evidence.

Cancel returns cancellation: state(enum revoked/unknown), release_confirmed(bool),
and optional error(exception class only). The binding is revoked before I/O. A
failed cancellation is not replayed automatically or called confirmed; further
owner failure may make even inspection unavailable. Repeated successful cancel
returns the same result. Inspection after cancellation remains available while
the owner is usable; new submission is refused. Direct Chief reconciliation is
separate. Closing the gateway explicitly revokes the owner's current permission.

## Durable events

The existing input.sqlite events table additionally accepts gateway_opened
({epoch,process}), gateway_bound({epoch,binding,principal,generation,commands}), and
gateway_intake({epoch,binding,principal,id}). commands maps IDs to SHA256 canonical
payload fingerprints. Credentials are never persisted. An intake event is intent,
not proof of successful admission; inspect the command row. Cancellation outcome
evidence uses existing coordinator events plus the returned response. If storage
fails, preserve the original journal and external error; do not fabricate a durable
success receipt. No power-loss guarantee is established.

gateway_extended adds {epoch,binding,principal,commands}, where commands contains
only the new command IDs/fingerprints. Existing command payloads remain immutable.

## Optional local HTTP adapter

InputHTTP starts only on explicit construction, on127.0.0.1 with an ephemeral port.
POST /v1/input requires bearer, exact loopback Host and application/json. Browser
Origin, transfer encoding, duplicate Host/Content-Type/Content-Length/Authorization,
extra fields, invalid UTF-8, duplicate JSON keys and nonfinite JSON are refused.
Body limit4096 bytes, socket timeout2s, maximum8 simultaneous request threads; excess
connections close. Responses are no-store. No credential-bearing access log.
There is no CLI, service installer, endpoint discovery file or public listener.

Status codes:200(result),400(invalid request),403(binding/credential refusal),
503(unavailable owner or internal failure); unsupported methods use the standard
server rejection. Receipt/cancellation status, not HTTP200 alone, defines outcome.
These limits are not denial-of-service resistance or a cancellation-latency promise.
The trusted local direct takeover path remains independent of HTTP/model queues.

Only SimulatedInput is accepted. Tests include actual Pi processes and owner death,
but real desktop delivery, hardware takeover, logon/resume and full SAS conformance
remain unverified. No capture, fuel, radio or driver-presence service is coupled here.
