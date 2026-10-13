"""Exercise 2: construct the actual Anthropic SDK Tool Runner.

Run: python exercises/check.py --exercise runner
The checker supplies an offline client, iterates your runner, and verifies its work.
"""


def build_runner(client, model, tools, question):
    """Return a Tool Runner that can answer the question using the supplied tools."""
    # TODO: Return client.beta.messages.tool_runner(...).
    # Pass model and tools unchanged; set max_tokens=512 and max_iterations=4.
    # Send question in one message with role='user' and content=question.
    # Return the runner itself. The caller iterates it to advance the loop.
    raise NotImplementedError("Build the runner, then check the real tool loop.")
