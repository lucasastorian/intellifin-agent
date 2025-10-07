def _is_notebook():
    """Detect if running in Jupyter/IPython notebook"""
    try:
        from IPython import get_ipython
        if get_ipython() is not None:
            return True
    except ImportError:
        pass
    return False


def print_messages(agent):
    """Print all messages with formatting and separators"""
    use_markdown = _is_notebook()

    if use_markdown:
        from IPython.display import display, Markdown

        output = []
        output.append("# CONVERSATION TRANSCRIPT\n")

        for i, msg in enumerate(agent.messages):
            if msg.role == "system":
                continue

            if i > 1:
                output.append("\n---\n")

            if msg.role == "user":
                output.append("### USER\n")
                output.append(msg.content + "\n")

            elif msg.role == "assistant":
                output.append("### ASSISTANT\n")
                if msg.content:
                    output.append(msg.content + "\n")

                if msg.actions:
                    output.append("\n**Actions:**\n")
                    for action in msg.actions:
                        output.append(f"- `{action.name}({action.body})`\n")

            elif msg.role == "tool":
                output.append("### TOOL\n")
                if msg.error:
                    output.append("**ERROR**\n\n")
                output.append(msg.content + "\n")

        display(Markdown("".join(output)))
    else:
        print("\n" + "=" * 80)
        print("CONVERSATION TRANSCRIPT")
        print("=" * 80 + "\n")

        for i, msg in enumerate(agent.messages):
            if msg.role == "system":
                continue

            if i > 1:
                print("\n" + "-" * 80 + "\n")

            if msg.role == "user":
                print("USER:")
                print(msg.content)

            elif msg.role == "assistant":
                print("ASSISTANT:")
                if msg.content:
                    print(msg.content)

                if msg.actions:
                    print("\nActions:")
                    for action in msg.actions:
                        print(f"  - {action.name}({action.body})")

            elif msg.role == "tool":
                print("TOOL:")
                if msg.error:
                    print("ERROR")
                print(msg.content)

        print("\n" + "=" * 80)
        print("END OF TRANSCRIPT")
        print("=" * 80 + "\n")
