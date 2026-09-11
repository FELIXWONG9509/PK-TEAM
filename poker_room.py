from dataclasses import dataclass, field
from typing import Optional
import time
import random


SUITS = ["S", "H", "D", "C"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]


@dataclass
class Player:
    player_id: str
    chips: int = 10000
    hole_cards: list = field(default_factory=list)
    active: bool = True
    last_action: str = ""
    current_bet: int = 0
    last_heartbeat: float = 0.0
    folded: bool = False


class PokerRoom:
    def __init__(self, max_players=8):
        self.max_players = max_players
        self.seats: list[Optional[Player]] = [None] * max_players
        self.pot = 0
        self.community_cards = []
        self.deck = []
        self.stage = "waiting"
        self.game_active = False
        self.current_turn_index = 0
        self.dealer_index = 0
        self.blind_small = 50
        self.blind_big = 100
        self.min_raise = 100
        self.current_bet = 0
        self.last_raiser_index = -1
        self.acted_this_round = set()

    # ---------- 玩家管理 ----------
    def player_count(self):
        return sum(1 for s in self.seats if s is not None)

    def is_full(self):
        return self.player_count() >= self.max_players

    def has_player(self, pid):
        return any(s is not None and s.player_id == pid for s in self.seats)

    def is_seat_empty(self, seat_index):
        if seat_index < 0 or seat_index >= self.max_players:
            return False
        return self.seats[seat_index] is None

    def add_player_at(self, pid, seat_index):
        if seat_index < 0 or seat_index >= self.max_players:
            return False
        if self.seats[seat_index] is not None:
            return False
        self.seats[seat_index] = Player(player_id=pid)
        self.seats[seat_index].last_heartbeat = time.time()
        return True

    def add_player(self, pid):
        for i in range(self.max_players):
            if self.seats[i] is None:
                self.seats[i] = Player(player_id=pid)
                self.seats[i].last_heartbeat = time.time()
                return True
        return False

    def remove_player(self, pid):
        for i in range(self.max_players):
            if self.seats[i] is not None and self.seats[i].player_id == pid:
                self.seats[i] = None
                return

    def get_player(self, pid):
        for s in self.seats:
            if s is not None and s.player_id == pid:
                return s
        return None

    def heartbeat(self, pid):
        p = self.get_player(pid)
        if p:
            p.last_heartbeat = time.time()

    def cleanup_stale(self, timeout=60):
        now = time.time()
        for i in range(self.max_players):
            s = self.seats[i]
            if s is not None and (now - s.last_heartbeat) > timeout:
                self.seats[i] = None

    # ---------- 游戏流程 ----------
    def _build_deck(self):
        deck = [f"{r}-{s}" for r in RANKS for s in SUITS]
        random.shuffle(deck)
        return deck

    def _next_active_index(self, start):
        for offset in range(1, self.max_players + 1):
            idx = (start + offset) % self.max_players
            s = self.seats[idx]
            if s is not None and s.active and not s.folded:
                return idx
        return -1

    def _active_indices(self):
        return [i for i, s in enumerate(self.seats) if s is not None and s.active and not s.folded]

    def start_new_hand(self):
        for s in self.seats:
            if s is not None:
                s.hole_cards = []
                s.active = True
                s.folded = False
                s.last_action = ""
                s.current_bet = 0
        self.pot = 0
        self.community_cards = []
        self.stage = "preflop"
        self.game_active = True
        self.acted_this_round = set()
        self.current_bet = 0
        self.last_raiser_index = -1

        active_indices = self._active_indices()
        if len(active_indices) < 2:
            self.game_active = False
            return

        self.deck = self._build_deck()
        for _ in range(2):
            for i in active_indices:
                self.seats[i].hole_cards.append(self.deck.pop())

        self.dealer_index = self._next_active_index(self.dealer_index)

        sb_idx = self._next_active_index(self.dealer_index)
        bb_idx = self._next_active_index(sb_idx)

        if sb_idx >= 0:
            self._post_bet(sb_idx, self.blind_small, "小盲")
        if bb_idx >= 0:
            self._post_bet(bb_idx, self.blind_big, "大盲")

        self.current_bet = self.blind_big
        self.current_turn_index = self._next_active_index(bb_idx)
        self.last_raiser_index = bb_idx

    def _post_bet(self, idx, amount, action_name):
        p = self.seats[idx]
        actual = min(amount, p.chips)
        p.chips -= actual
        p.current_bet += actual
        self.pot += actual
        p.last_action = action_name

    def is_my_turn(self, pid):
        if not self.game_active:
            return False
        if self.current_turn_index < 0 or self.current_turn_index >= self.max_players:
            return False
        s = self.seats[self.current_turn_index]
        return s is not None and s.player_id == pid and not s.folded

    def player_fold(self, pid):
        p = self.get_player(pid)
        if p:
            p.folded = True
            p.last_action = "暂不参与"
            self._advance_turn()

    def player_check_or_call(self, pid):
        p = self.get_player(pid)
        if not p:
            return
        to_call = self.current_bet - p.current_bet
        if to_call <= 0:
            p.last_action = "确认"
        else:
            actual = min(to_call, p.chips)
            p.chips -= actual
            p.current_bet += actual
            self.pot += actual
            p.last_action = f"确认 ({actual})"
        self._advance_turn()

    def player_raise(self, pid, amount):
        p = self.get_player(pid)
        if not p:
            return
        to_call = self.current_bet - p.current_bet
        total = to_call + amount
        actual = min(total, p.chips)
        p.chips -= actual
        p.current_bet += actual
        self.pot += actual
        self.current_bet = p.current_bet
        p.last_action = f"调整至 {p.current_bet}"
        self.last_raiser_index = self._find_index(pid)
        self.acted_this_round = {self.last_raiser_index}
        self._advance_turn()

    def _find_index(self, pid):
        for i, s in enumerate(self.seats):
            if s is not None and s.player_id == pid:
                return i
        return -1

    def _advance_turn(self):
        active = self._active_indices()
        if len(active) <= 1:
            self._end_hand()
            return

        if self._betting_round_complete():
            self._next_stage()
            return

        idx = self._next_active_index(self.current_turn_index)
        self.current_turn_index = idx

    def _betting_round_complete(self):
        active = self._active_indices()
        if not active:
            return True
        for i in active:
            s = self.seats[i]
            if s.current_bet != self.current_bet and not s.folded:
                return False
        for i in active:
            if i not in self.acted_this_round and i != self.last_raiser_index:
                return False
        return True

    def _next_stage(self):
        for s in self.seats:
            if s is not None:
                s.current_bet = 0
                s.last_action = ""
        self.acted_this_round = set()
        self.current_bet = 0
        self.last_raiser_index = -1

        if self.stage == "preflop":
            self.stage = "flop"
            self.community_cards.extend([self.deck.pop() for _ in range(3)])
        elif self.stage == "flop":
            self.stage = "turn"
            self.community_cards.append(self.deck.pop())
        elif self.stage == "turn":
            self.stage = "river"
            self.community_cards.append(self.deck.pop())
        elif self.stage == "river":
            self._end_hand()
            return

        self.current_turn_index = self._next_active_index(self.dealer_index)

    def _end_hand(self):
        active = self._active_indices()
        if len(active) == 1:
            winner_idx = active[0]
            self.seats[winner_idx].chips += self.pot
            self.seats[winner_idx].last_action = "获得资源池"
        else:
            winner_idx = self._simple_showdown(active)
            self.seats[winner_idx].chips += self.pot
            self.seats[winner_idx].last_action = "获得资源池"

        self.pot = 0
        self.stage = "showdown"
        self.game_active = False
        self.current_turn_index = -1

    def _simple_showdown(self, active_indices):
        best_idx = active_indices[0]
        best_score = -1
        for i in active_indices:
            score = self._hand_score(self.seats[i].hole_cards)
            if score > best_score:
                best_score = score
                best_idx = i
        return best_idx

    def _hand_score(self, hole):
        all_cards = hole + self.community_cards
        rank_order = {r: i for i, r in enumerate(RANKS)}
        max_rank = max((rank_order.get(c.split("-")[0], 0) for c in all_cards), default=0)
        return max_rank
