"""Reference answer. Edit exercises/runner_exercise.py for the exercise."""


def build_runner(client, model, tools, question):
    """Create the SDK runner; iterating it executes tools and continues the chat."""
    return client.beta.messages.tool_runner(
        model=model,
        max_tokens=512,
        max_iterations=4,
        tools=tools,
        messages=[{"role": "user", "content": question}],
    )
