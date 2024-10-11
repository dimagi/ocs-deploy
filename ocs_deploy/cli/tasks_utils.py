from invoke import Exit


def confirm(message, _exit=True, exit_message="Done"):
    response = input(f"{message} (y/n): ")
    confirmed = response.lower() == "y"
    if not confirmed and _exit:
        raise Exit(exit_message, -1)
    return confirmed
