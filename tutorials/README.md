# Tool Runner: four self-paced labs

Keep the working bot as the introduction. Then choose one small lab, edit code that powers the bot, inspect the result, and check the behavior. The modules are independent: you can stop after any checkpoint and return without replaying earlier modules.

These are tutorials for individual learning, not a timed instructor-led workshop. The approximate times below are planning estimates; they have not been measured with learners.

| Lab | Approximate time | You write | Evidence of learning |
| --- | --- | --- | --- |
| [1. Why Tool Runner?](01-why-tool-runner.ipynb) | 15 minutes | `build_runner` | A manual loop and your SDK runner save the same return; available stock changes the outcome. |
| [2. Build the tool your bot uses](02-build-tools.ipynb) | 20 minutes | `make_inventory_tool` | Tool Runner calls your typed function; its actual inventory result controls the next action. |
| [3. Prove the action happened](03-verify-actions.ipynb) | 20 minutes | `verify_return` | Your check rejects a missing write and mismatched identifiers, while accepting a matching saved record. |
| [4. Make the bot follow your data](04-make-it-yours.ipynb) | 20 minutes | `available_stock` | Subtracting reserved units changes the bot's behavior without changing its conversation loop. |

## Open a lab in Colab

1. Choose **Open in Colab** from the [lab website](https://dannymsullivan.github.io/tool-runner-lab/), or [open Lab 1 directly](https://colab.research.google.com/github/dannymsullivan/tool-runner-lab/blob/main/tutorials/01-why-tool-runner.ipynb).
2. Alternatively, download a notebook and choose **File → Upload notebook** in [Google Colab](https://colab.research.google.com/).
3. Choose **File → Save a copy in Drive** to keep your edited version.
4. Run setup and preflight, make a prediction, and complete the editable function.
5. Run the function cell, click **Run my bot**, and run the checkpoint.

Basic Python functions and dictionaries are the prerequisite. Learner notebooks intentionally contain an unfinished function. A failed first check is expected and gives a next action. Do not use “Run all” as the main learning path.

The inline interface runs in the notebook using standard Jupyter widgets. Each click uses the latest executed learner function and starts a fresh store. Rerun an edited definition before clicking the same button. If widgets do not render in your environment, the notebook includes an ordinary Python call to the exact same bot and evidence display.

## What is real, and what is scripted?

Practice mode uses scripted model replies through an offline transport. Anthropic's actual SDK dispatches actual Python tools against a real local SQLite database. Package installation needs an internet connection; practice runs do not call Claude and require no API key.

The model script reacts to the inventory result. It supports the supplied customer scenario and variations in stock, reservations, service failure, and database-write behavior. Arbitrary new prompts or tools require a matching script or a live model. The fourth module includes an explicit optional live call with a private key prompt; model behavior and wording may differ.

The visible `build_runner` function owns the SDK call. The display helpers supply the interface and fresh practice data. The function you edit is injected into that run, not exercised separately from a reference bot.

## Recovery and continuation

Every notebook has a hint, a readable solution, and an explicit recovery cell. Setting `USE_REFERENCE=True` and running that cell replaces only the exercise function in the running notebook. The code you typed stays in its cell. Checks and bot output identify a loaded reference; rerunning your own function switches back to your implementation.

Complete runnable references are separate files:

- [Lab 1 reference](01-why-tool-runner.reference.ipynb)
- [Lab 2 reference](02-build-tools.reference.ipynb)
- [Lab 3 reference](03-verify-actions.reference.ipynb)
- [Lab 4 reference](04-make-it-yours.reference.ipynb)

After a disconnected runtime, rerun setup, the supplied runner if present, your edited function, and the checkpoint. Save a passing notebook and its observed outputs before leaving. There is no persisted customer database: output records the evidence from a particular fresh run.

## Connection to the documentation

The [Tool Runner guide](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-runner) supplies the technical reference. The labs organize it around decisions the learner must make:

| Concept | Where it appears | Learner action |
| --- | --- | --- |
| Automated tool loop and message handling | Lab 1 | Compare explicit bookkeeping with the visible SDK call. |
| Typed tools, docstrings, generated schemas | Lab 2 | Implement a tool, inspect its schema, observe SDK dispatch. |
| Tool errors and iteration | Labs 2–3 | Inspect real error results and distinguish failure from zero stock. |
| Application outcome verification | Lab 3 | Match a reported action to saved identifiers. This is application logic, not a Tool Runner guarantee. |
| Reusable tools and SDK orchestration | Lab 4 | Change a data adapter while retaining the loop and behavioral checks. |

The existing `tool-runner-lab.ipynb` remains a complete guided reference with deeper experiments on iteration limits and message tracing. The series focuses on learner edits rather than claiming to cover every SDK feature.

## Validation and limits

`tests/test_tutorials.py` verifies the generated files match their source, all eight notebooks have valid notebook structure, and their executable code works in clean local Python namespaces. It checks that unfinished exercises fail with feedback, all complete references pass, changed learner functions affect actual SDK dispatch and saved outcomes, and resources close after success or failure. A widget callback test changes the learner function after constructing the interface and proves the next click uses the new implementation. Practice tests block network connections.

Optional live calls require explicit opt-in. Tests check that an empty key makes no call and that error feedback does not print secrets. No paid model call is required to complete any module.

Hosted Colab execution and learner timing/usability have not yet been validated. Local execution skips package-install magic but executes embedded setup and lesson code. The notebook uses [Colab's widget support](https://colab.research.google.com/github/jupyter-widgets/ipywidgets/blob/master/docs/source/examples/Widget%20Basics.ipynb), with a plain-cell fallback. A fresh hosted Colab run and an observed independent learner session are the next validation steps.

Regenerate the notebooks from the app folder with `python tutorial_series.py`. Changes to lesson text and code belong in `tutorial_series.py` and `tutorial_runtime.py`; regenerate rather than hand-editing the eight copies.
