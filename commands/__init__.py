# Here you define the commands that will be added to your add-in.
from .gearProfile import entry as gearProfile

# Fusion will automatically call the start() and stop() functions.
commands = [
    gearProfile,
]


# Assumes you defined a "start" function in each of your modules.
# The start function will be run when the add-in is started.
def start():
    for command in commands:
        command.start()


# Assumes you defined a "stop" function in each of your modules.
# The stop function will be run when the add-in is stopped.
def stop():
    for command in commands:
        command.stop()
