# Tool Runner tutorial design

## Audience and outcome

For a developer who knows Python functions and dictionaries but has not implemented a model/tool conversation loop. By the end, the learner can explain which coordination Tool Runner removes, implement a tool used by a bot, verify its effects, and adapt its data source without rewriting the loop.

This is a self-paced tutorial series. Module boundaries create stopping points; total elapsed time is not a learning objective. Per-module estimates need learner validation.

## Start with the whole application

The opening support bot establishes a concrete problem: a customer wants a return only if a replacement is unavailable. Learners see the reply and the database outcome, then change stock and compare. The five short introductory cells connect that experience to Python, tool schemas, the runner, changing data, and saved state.

From there, a simple list offers four independently runnable notebooks. Setup is supplied and collapsed. Each notebook follows the same rhythm: prepare, predict, edit one function, run the bot, inspect evidence, check, and transfer. Hints and references appear after the attempt; learners can opt into recovery without losing their editable code.

## Objectives and evidence

| Module | Concept from the documentation | Learner action | Observable evidence |
|---|---|---|---|
| Why Tool Runner? | Repeated model requests, tool dispatch, result matching, and conversation history | Run the manual loop; implement build_runner | Same three model turns and saved return; stocked case creates no return |
| Build the tool your bot uses | Types/docstrings become a schema; the SDK dispatches Python tools | Implement make_inventory_tool | Generated product schema; actual learner tool supplies counts 0 and 3; an outage stays an error |
| Prove the action happened | A final model message is not application-level verification | Implement verify_return | Both IDs must match the same stored row; missing write and wrong-order cases fail |
| Make the bot follow your data | Tool contracts separate application data from conversation coordination | Implement available_stock | Reservation changes alter the tool result and return outcome; excess reservations clamp to zero |

Primary documentation: [Tool Runner](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-runner), [manual tool handling](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls).

The series introduces the core loop and selected application concerns. It is not an exhaustive reference for every Tool Runner option. Trace inspection and iteration-limit comparisons remain available as related browser experiments.

## Recovery and persistence

Each notebook is self-contained and has a complete reference partner. Recovery is an explicit action, never a hidden fallback. Output identifies loaded reference code. Redefining the learner function restores their implementation. A normal Python cell provides the same bot behavior if widgets fail to render.

Learners save a copy in Drive to keep their code and outputs. Runtime state is temporary; each bot call uses a new store. The browser remembers only the last module opened, not inferred completion. The published artifact must not imply that a download or a supplied-code preview proves competence.

## Engineering evidence

The UI invokes the latest executed learner function, so editing code changes the application. Checks include multiple inputs and negative cases. Offline practice uses scripted model responses with the actual SDK and database; its results concern software behavior, not model reasoning. A private, explicit key prompt enables optional live exploration in the final module.

Local tests cover standalone embedded notebook execution, schema validity, actual tool dispatch, callback updates, starter feedback, reference recovery, missing writes, outages, reserved-stock boundaries, and resource cleanup. HTTP tests cover all eight downloads and invalid paths. The opening application and notebook navigation are checked in the browser.

## Remaining validation before publication

1. Upload each notebook to hosted Colab and verify setup, widget rendering, one learner edit, checkpoint feedback, recovery, and reconnection. Local execution cannot establish hosted UI compatibility.
2. Observe a new learner using the opening demo and first notebook without coaching. Record where they hesitate and whether they can explain who requests, executes, and verifies an action.
3. Ask the learner to change the fourth module without its reference. Use the changed bot outcome and boundary-case explanation as evidence of transfer.
4. Record actual completion times; adjust module estimates and hints from that evidence.
5. Exercise optional live mode with an authorized account and available model, checking behavior rather than exact wording.

The site includes direct GitHub-backed Colab links and optional downloads. Hosted Colab execution and learner rehearsal still need the checks above. The GitHub Pages edition explicitly labels browser examples as recordings; the local Python server and Colab notebooks execute real code.
