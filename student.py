"""Example client."""
import asyncio
import getpass
import json
import os
import time

import websockets
import math

import game
from tree_search import *
from consts import *
from typing import Union, Callable


class PointsGraph(SearchDomain):
    def __init__(self, connections, coordinates):
        self.connections = connections
        self.coordinates = coordinates

    def actions(self, point) -> list:
        actlist = []
        for (P1, P2, C) in self.connections:
            if P1 == point:
                actlist += [(P1, P2)]
            elif P2 == point:
                actlist += [(P2, P1)]
        return actlist

    def result(self, point, action) -> str:
        (P1, P2) = action
        if P1 == point:
            return P2

    def cost(self, point, action) -> Union[int, None]:
        (A1, A2) = action

        if A1 != point:
            return None

        for P1, P2, C in self.connections:
            if (P1, P2) in [(A1, A2), (A2, A1)]:
                return C

    def heuristic(self, point, goal_point) -> float:
        x1, y1 = self.coordinates[point]
        x2, y2 = self.coordinates[goal_point]

        return abs(x2 - x1) + abs(y2 - y1)

    def satisfies(self, point, goal_point) -> bool:
        return goal_point == point


class Agent:
    def __init__(self):
        self.state: dict = {}
        self.pos: list[int] = []
        self.last_pos: list[int] = []
        self.dir: Direction = Direction.EAST
        self.enemies: list[dict] = []
        self.ts: float = 0.0
        self.map: list = []
        self.map_size: list = []
        self.pos_rocks: list = []
        self.previous_positions: list[list[int]] = []
        self.chosen_enemy: dict = {}
        self.steps: int = 0
        self.enemies_stuck: list = []

    def get_digdug_direction(self, new: list[int], test: bool = False) -> Direction:
        """
        Get DigDug position based on last/current or current/new positions.

        The ``test`` argument decides whether this function call is a test or not.\n
        If ``true``, it means that it is called from a place where several conditions are being tested,
        and it does not represent the current DigDug state.\n
        If ``false``, it is used to set the current DigDug direction.
        :param new: Current or new position, depending on the context
        :type new: list[int]
        :param test: Argument
        :type test: bool
        :return: DigDug direction
        :rtype: Direction
        """
        last = self.last_pos if test is False else self.pos

        positions_mapping = {
            lambda: new[0] == last[0] and new[1] < last[1]: Direction.NORTH,
            lambda: new[0] == last[0] and new[1] > last[1]: Direction.SOUTH,
            lambda: new[0] < last[0]: Direction.WEST,
            lambda: new[0] > last[0]: Direction.EAST
        }

        # When the game/level starts, it has no last position
        if not last:
            return self.dir

        return next((direction for condition, direction in positions_mapping.items() if condition()), self.dir)

    def is_digdug_in_front_of_enemy(self, enemy: dict) -> bool:
        """
        Check if DigDug is looking at the enemy through its direction.\n
        Useful for testing fire conditions.
        :param enemy: Enemy to check against.
        :type enemy: dict
        :return: Either True or False.
        :rtype: bool
        """
        direction_mapping: dict[Direction, Callable[[int, int, int, int], bool]] = {
            Direction.NORTH: lambda d0, d1, e0, e1: (d0 == e0 and d1 > e1),
            Direction.SOUTH: lambda d0, d1, e0, e1: (d0 == e0 and d1 < e1),
            Direction.WEST: lambda d0, d1, e0, e1: (d1 == e1 and d0 > e0),
            Direction.EAST: lambda d0, d1, e0, e1: (d1 == e1 and d0 < e0),
        }

        return direction_mapping[self.dir](self.pos[0], self.pos[1], enemy["pos"][0], enemy["pos"][1])

    def are_digdug_and_enemy_facing_each_other(self, enemy: dict) -> bool:
        """
        Check if DigDug and the enemy are facing each other (i.e., are in opposite directions).
        :param enemy: Enemy to check against.
        :type enemy: dict
        :return: Either True or False.
        :rtype: bool
        """
        digdug_direction: Direction = self.dir
        enemy_direction: Direction = enemy["dir"]

        if enemy_direction > 1:
            return digdug_direction == enemy_direction - 2
        return digdug_direction == enemy_direction + 2

    def is_map_digged_to_direction(self, direction: Direction) -> bool:
        """
        Check whether the map point next to DigDug in a certain direction is digged or not.
        :param direction: Direction to check against.
        :type direction: Direction
        :return: Either True or False.
        :rtype: bool
        """
        direction_mapping: dict[Direction, tuple[int, int]] = {
            Direction.NORTH: (0, -1),
            Direction.SOUTH: (0, 1),
            Direction.WEST: (-1, 0),
            Direction.EAST: (1, 0),
        }
        dx, dy = direction_mapping[direction]

        if self.map[self.pos[0] + dx][self.pos[1] + dy] == 0:
            return True
        return False

    def dig_map(self, direction: Union[Direction, None], fallback: Union[list[Direction], None] = None) -> str:
        """
        Dig the map in a certain direction.\n
        It checks multiple conditions, such as enemies, fire, map boundaries and rocks.
        :param direction: Direction to dig.
        :type direction: Direction
        :param fallback: Fallback directions to dig in case the main direction is not possible.
        :type fallback: list[Direction]
        :return: Key to send to the server.
        :rtype: str
        """
        if direction is None:
            return ""
        if fallback is None:
            fallback = []

        direction_mapping: dict[Direction, tuple[int, int, str]] = {
            Direction.NORTH: (0, -1, "w"),
            Direction.SOUTH: (0, 1, "s"),
            Direction.WEST: (-1, 0, "a"),
            Direction.EAST: (1, 0, "d"),
        }

        dx, dy, key = direction_mapping[direction]
        x = self.pos[0] + dx
        y = self.pos[1] + dy

        if (0 <= x < self.map_size[0] and 0 <= y < self.map_size[1]
                and not self.will_enemy_fire_at_digdug([x, y])
                and [x, y] not in self.pos_rocks) and not self.check_dist_all_enemies([x, y]):
            self.map[x][y] = 0

            print("Real move after checks: ", direction.name)
            self.steps += 1
            return key

        return self.dig_map(fallback[0] if len(fallback) > 0 else None, fallback[1:])

    def check_dist_all_enemies(self, digdug_pos: list[int]) -> bool:
        """
        Check if DigDug will be too close to any enemy, based on the position passed as argument.
        :param digdug_pos: New DigDug position.
        :type digdug_pos: list[int]
        :return: Either True or False.
        :rtype: bool
        """
        too_close = False
        x, y = digdug_pos
        for enemy in self.enemies:
            if enemy["name"] == "Fygar" and self.map[x][y] == 1 and not self.will_enemy_fire_at_digdug([x, y]):
                too_close = False
            elif Direction.NORTH and ((enemy["pos"][0] == x and enemy["pos"][1] == y) or (enemy["pos"][0] + 1 == x and enemy["pos"][1] == y) or (enemy["pos"][0] - 1 == x and enemy["pos"][1] == y) or (enemy["pos"][0] == x and enemy["pos"][1] + 1 == y)):
                too_close = True
            elif Direction.SOUTH and ((enemy["pos"][0] == x and enemy["pos"][1] == y) or (enemy["pos"][0] + 1 == x and enemy["pos"][1] == y) or (enemy["pos"][0] - 1 == x and enemy["pos"][1] == y) or (enemy["pos"][0] == x and enemy["pos"][1] - 1 == y)):
                too_close = True
            elif Direction.EAST and ((enemy["pos"][0] == x and enemy["pos"][1] == y) or (enemy["pos"][0] - 1 == x and enemy["pos"][1] == y) or (enemy["pos"][0] == x and enemy["pos"][1] + 1 == y) or (enemy["pos"][0] == x and enemy["pos"][1] - 1 == y)):
                too_close = True
            elif Direction.WEST and ((enemy["pos"][0] == x and enemy["pos"][1] == y) or (enemy["pos"][0] + 1 == x and enemy["pos"][1] == y) or (enemy["pos"][0] == x and enemy["pos"][1] + 1 == y) or (enemy["pos"][0] == x and enemy["pos"][1] - 1 == y)):
                too_close = True

        return too_close

    def get_lower_cost_enemy(self, last_enemy: Union[dict, None] = None) -> dict:
        """
        Get the enemy with the lowest cost to DigDug.\n
        It uses the A* algorithm to calculate the cost, even though its benefit is bare minimal.
        :return: Enemy with the lowest cost.
        :rtype: dict
        """
        connections = [("digdug", enemy["id"], math.hypot(enemy["pos"][0] - self.pos[0], enemy["pos"][1] - self.pos[1])) for enemy in self.enemies]
        coordinates = {character["id"]: tuple(character["pos"]) for character in self.enemies}
        coordinates["digdug"] = tuple(self.pos)

        map_points = PointsGraph(connections, coordinates)
        chosen_enemy = {"pos": [0, 0], "cost": float("inf")}

        for enemy in self.enemies:
            if ("traverse" not in enemy or self.map[enemy['pos'][0]][enemy['pos'][1]] == 0) or len(self.enemies) == 1:
                p = SearchProblem(map_points, 'digdug', enemy["id"])
                t = SearchTree(p, 'a*')
                t.search()

                enemy["x_dist"]: int = enemy["pos"][0] - self.pos[0]
                enemy["y_dist"]: int = enemy["pos"][1] - self.pos[1]
                enemy["dist"]: int = abs(enemy["x_dist"]) + abs(enemy["y_dist"])
                enemy["cost"] = t.cost

                if (last_enemy is not None and enemy["id"] != last_enemy["id"]) or last_enemy is None:
                    if enemy["cost"] < chosen_enemy["cost"]:
                        chosen_enemy = enemy
        return chosen_enemy

    def will_enemy_fire_at_digdug(self, digdug_new_pos: list[int]) -> bool:
        """
        Check if any enemy will fire at DigDug in the next move.
        :param digdug_new_pos: DigDug new position.
        :type digdug_new_pos: list[int]
        :return: Either True or False.
        :rtype: bool
        """
        direction_mapping: dict[Direction, Callable[[int, int, int, int], bool]] = {
            Direction.NORTH: lambda dx, dy, ex, ey: (dx == ex and dy in (ey-1, ey-2, ey-3, ey-4)),
            Direction.SOUTH: lambda dx, dy, ex, ey: (dx == ex and dy in (ey+1, ey+2, ey+3, ey+4)),
            Direction.EAST: lambda dx, dy, ex, ey: (dy == ey and dx in (ex+1, ex+2, ex+3, ex+4)),
            Direction.WEST: lambda dx, dy, ex, ey: (dy == ey and dx in (ex-1, ex-2, ex-3, ex-4)),
        }

        return any([
            direction_mapping[enemy["dir"]](digdug_new_pos[0], digdug_new_pos[1], enemy["pos"][0], enemy["pos"][1])
            for enemy in self.enemies
            if "name" in enemy and enemy["name"] == "Fygar"
        ])

    def is_in_loop(self) -> bool:
        """
        Check if DigDug is in a loop.
        Useful when enemies get smart.\n
        I don't know if it's working properly.
        :return: Either True or False.
        :rtype: bool
        """
        return self.previous_positions.count(self.pos) > 5

    def get_key(self, state: dict) -> str:
        """
        Get the key to send to the server based on the current state.\n
        It contains attack testing, map digging and enemy following.
        Also, it checks if DigDug is in a loop, and whether DigDig is following an enemy in a bugged way or not.
        :param state: Current state.
        :type state: dict
        :return: Key to send to the server (w, a, s, d, A).
        :rtype: str
        """
        if "digdug" in state:
            self.ts: float = state["ts"]
            self.last_pos: list[int] = self.pos
            self.pos: list[int] = state["digdug"]
            self.dir: Direction = self.get_digdug_direction(self.pos)
            print(self.dir.name)
            self.enemies: list[dict] = state["enemies"]
            self.previous_positions.append(self.pos)
            if 'rocks' in state:
                self.pos_rocks: list = [rock["pos"] for rock in state["rocks"]]

            last_enemy = self.chosen_enemy
            self.chosen_enemy = self.get_lower_cost_enemy()

            if "dist" not in self.chosen_enemy:
                return ""

            print("STEPS: ", self.steps)
            print("CHOSEN ENEMY: ", self.chosen_enemy)
            print("LAST ENEMY: ", last_enemy)

            if "id" in last_enemy and "id" in self.chosen_enemy:

                if self.chosen_enemy["id"] == last_enemy["id"] and self.steps > 300:
                    print("STUCK")
                    print("ENEMIES STUCK: ", self.chosen_enemy["id"])
                    self.previous_positions = []
                    self.steps = 0
                    if len(self.enemies) > 1:
                        self.enemies_stuck.append(self.chosen_enemy["id"])
                        self.chosen_enemy = self.get_lower_cost_enemy(last_enemy)
                        while self.chosen_enemy["id"] in self.enemies_stuck:
                            self.chosen_enemy = self.get_lower_cost_enemy(self.chosen_enemy)
                        if "id" not in self.chosen_enemy:
                            self.chosen_enemy = self.get_lower_cost_enemy()
                        print("NEW CHOSEN ENEMY: ", self.chosen_enemy)
                    else:
                        self.dig_map(Direction.NORTH, [Direction.WEST, Direction.EAST, Direction.SOUTH])
                elif self.chosen_enemy["id"] != last_enemy["id"]:
                    print("DIFFERENT ENEMY")
                    self.steps = 0
                else:
                    print("SAME ENEMY")

            print("\n--------------------")
            print("\npos digdug: ", self.pos)
            print("\nenemies: " + str(self.enemies))
            print("\nchosen enemy:", self.chosen_enemy)
            print("\nprevious positions: ", self.previous_positions)


            x_dist: int = self.chosen_enemy["x_dist"]
            y_dist: int = self.chosen_enemy["y_dist"]
            dist: int = self.chosen_enemy["dist"]

            # Change the direction when it bugs and just follows the enemy
            if "dir" in self.chosen_enemy and self.dir == self.chosen_enemy["dir"]:
                if x_dist == 1:
                    if y_dist in (0, -1, 1):
                        return self.dig_map(Direction.NORTH, [Direction.SOUTH, Direction.WEST, Direction.EAST])
                    return self.dig_map(Direction.EAST, [Direction.WEST, Direction.NORTH, Direction.SOUTH])

                elif x_dist == -1:
                    if y_dist in (-1, 0, 1):
                        return self.dig_map(Direction.SOUTH, [Direction.NORTH, Direction.EAST, Direction.WEST])
                    return self.dig_map(Direction.WEST, [Direction.EAST, Direction.SOUTH, Direction.NORTH])

                elif y_dist == 1:
                    if x_dist in (-1, 0, 1):
                        return self.dig_map(Direction.EAST, [Direction.WEST, Direction.NORTH, Direction.SOUTH])
                    return self.dig_map(Direction.SOUTH, [Direction.NORTH, Direction.EAST, Direction.WEST])

                elif y_dist == -1:
                    if x_dist in (-1, 0, 1):
                        return self.dig_map(Direction.WEST, [Direction.EAST, Direction.SOUTH, Direction.NORTH])
                    return self.dig_map(Direction.NORTH, [Direction.SOUTH, Direction.EAST, Direction.WEST])

            if self.is_in_loop():
                self.previous_positions = []
                if self.dir == Direction.EAST:
                    return self.dig_map(Direction.EAST, [Direction.WEST, Direction.NORTH, Direction.SOUTH])
                elif self.dir == Direction.WEST:
                    return self.dig_map(Direction.WEST, [Direction.EAST, Direction.NORTH, Direction.SOUTH])
                elif self.dir == Direction.NORTH:
                    return self.dig_map(Direction.NORTH, [Direction.SOUTH, Direction.WEST, Direction.EAST])
                elif self.dir == Direction.SOUTH:
                    return self.dig_map(Direction.SOUTH, [Direction.NORTH, Direction.WEST, Direction.EAST])

            # Move around the map
            def direction_mapping() -> tuple:
                if abs(x_dist) >= abs(y_dist):
                    if x_dist > 0:
                        return Direction.EAST, [Direction.SOUTH, Direction.NORTH, Direction.WEST]
                    return Direction.WEST, [Direction.NORTH, Direction.SOUTH, Direction.EAST]
                else:
                    if y_dist > 0:
                        return Direction.SOUTH, [Direction.EAST, Direction.WEST, Direction.NORTH]
                    return Direction.NORTH, [Direction.EAST, Direction.WEST, Direction.SOUTH]

            chosen_dir, fallback = direction_mapping()

            if dist <= 3:
                if self.is_digdug_in_front_of_enemy(self.chosen_enemy) \
                        and self.is_map_digged_to_direction(chosen_dir) \
                        and not self.will_enemy_fire_at_digdug([self.pos[0], self.pos[1]]) and not self.check_dist_all_enemies(self.pos):
                    self.previous_positions = []
                    self.steps +=1
                    return "A"
            return self.dig_map(chosen_dir, fallback)

        else:
            self.map: list[list[int]] = state["map"]
            self.map_size: list[int, int] = state["size"]
            self.previous_positions = []

        return ""


async def agent_loop(server_address="localhost:8000", agent_name="student"):
    agent = Agent()
    async with websockets.connect(f"ws://{server_address}/player") as websocket:
        # Receive information about static game properties
        await websocket.send(json.dumps({"cmd": "join", "name": agent_name}))

        starttime = time.monotonic()
        while True:
            try:
                # Receive game update.
                state: dict = json.loads(
                    await websocket.recv()
                )

                key: str = agent.get_key(state)
                await websocket.send(
                    json.dumps({"cmd": "key", "key": key})
                )

                # Time sync
                time.sleep((1 / game.GAME_SPEED) - ((time.monotonic() - starttime) % (1 / game.GAME_SPEED)))
            except websockets.exceptions.ConnectionClosedOK:
                print("Server has cleanly disconnected us")
                return


# DO NOT CHANGE THE LINES BELLOW
# You can change the default values using the command line, example:
# $ NAME='arrumador' python3 client.py
loop = asyncio.get_event_loop()
SERVER = os.environ.get("SERVER", "localhost")
PORT = os.environ.get("PORT", "8000")
NAME = os.environ.get("NAME", getpass.getuser())
loop.run_until_complete(agent_loop(f"{SERVER}:{PORT}", NAME))
