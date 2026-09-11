import streamlit as st
import random
import string
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional
from streamlit_server_state import server_state, server_state_lock
from streamlit_autorefresh import st_autorefresh

# ============================================================
# 页面伪装配置
# ============================================================
st.set_page_config(
    page_title="团队协作看板",
    page_icon="📊",
    layout="wide",
)

hide_style = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
</style>
"""
st.markdown(hide_style, unsafe_allow_html=True)

st.title("📊 团队协作看板 v2.3")

# ============================================================
# 扑克逻辑
# ============================================================
SUITS = ["S", "H", "D", "C"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]
AI_NAMES = ["A001", "A002", "A003", "A004", "A005"]


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
    is_ai: bool = False


class PokerRoom:
    def __init__(self, max_players=8):
        self.max_players = max_players
        self.seats: list = [None] * max_players
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

    def add_player_at(self, pid, seat_index, is_ai=False):
        if seat_index < 0 or seat_index >= self.max_players:
            return False
        if self.seats[seat_index] is not None:
            return False
        self.seats[seat_index] = Player(player_id=pid, is_ai=is_ai)
        self.seats[seat_index].last_heartbeat = time.time()
        return True

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

    def cleanup_stale(self, timeout=15):
        now = time.time()
        for i in range(self.max_players):
            s = self.seats[i]
            if s is None or s.is_ai:
                continue
            if (now - s.last_heartbeat) > timeout:
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
        self.acted_this_round.add(self._find_index(pid))
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


# ============================================================
# AI 决策
# ============================================================

def evaluate_hand_strength(hole, community):
    if not hole or len(hole) < 2:
        return 0.0
    rank_order = {r: i for i, r in enumerate(RANKS)}

    def card_rank(c):
        return rank_order.get(c.split("-")[0], 0)

    def card_suit(c):
        return c.split("-")[1]

    r1, r2 = card_rank(hole[0]), card_rank(hole[1])
    s1, s2 = card_suit(hole[0]), card_suit(hole[1])

    score = 0.0
    score += max(r1, r2) / 12 * 0.3
    if r1 == r2:
        score += 0.35 + (r1 / 12) * 0.15
    if s1 == s2:
        score += 0.08
    if abs(r1 - r2) == 1:
        score += 0.05

    if community:
        all_ranks = [card_rank(c) for c in community] + [r1, r2]
        all_suits = [card_suit(c) for c in community] + [s1, s2]
        rank_counts = Counter(all_ranks)
        max_count = max(rank_counts.values())
        if max_count >= 2:
            score += (max_count - 1) * 0.15
        suit_counts = Counter(all_suits)
        max_suit = max(suit_counts.values())
        if max_suit >= 4:
            score += 0.15
        if max_suit >= 5:
            score += 0.25

    return min(score, 1.0)


def ai_take_action(room, ai_player):
    pid = ai_player.player_id
    to_call = room.current_bet - ai_player.current_bet
    strength = evaluate_hand_strength(ai_player.hole_cards, room.community_cards)
    strength += random.uniform(-0.1, 0.1)
    strength = max(0.0, min(1.0, strength))
    cost_ratio = to_call / max(ai_player.chips, 1)

    if strength > 0.65:
        if ai_player.chips > room.current_bet + room.blind_big * 2:
            raise_amount = room.blind_big * random.choice([1, 2, 3])
            room.player_raise(pid, raise_amount)
        else:
            room.player_check_or_call(pid)
    elif strength > 0.35:
        if to_call == 0:
            room.player_check_or_call(pid)
        elif cost_ratio < 0.15:
            room.player_check_or_call(pid)
        else:
            room.player_fold(pid)
    else:
        if to_call == 0:
            room.player_check_or_call(pid)
        elif cost_ratio < 0.05:
            room.player_check_or_call(pid)
        else:
            room.player_fold(pid)


def process_ai_actions(room):
    for _ in range(100):
        if not room.game_active:
            break
        idx = room.current_turn_index
        if idx < 0 or idx >= room.max_players:
            break
        seat = room.seats[idx]
        if seat is None or not seat.is_ai:
            break
        ai_take_action(room, seat)


# ============================================================
# Streamlit UI
# ============================================================

# ---------- 玩家身份 ----------
if "player_id" not in st.session_state:
    if "pid" in st.query_params:
        st.session_state.player_id = st.query_params["pid"]
    else:
        new_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        st.session_state.player_id = new_id
        st.query_params["pid"] = new_id

my_id = st.session_state.player_id

# ---------- 模式（用 session_state，比 URL 参数可靠）----------
if "mode" not in st.session_state:
    st.session_state.mode = "multi"

mode = st.session_state.mode

# 只有多人模式才启用自动刷新（心跳用）
if mode == "multi":
    st_autorefresh(interval=10000, key="hb_refresh")

# ---------- 单人模式 ----------
if mode == "solo":
    with server_state_lock["solo_rooms"]:
        if "solo_rooms" not in server_state:
            server_state.solo_rooms = {}
        if my_id not in server_state.solo_rooms:
            room = PokerRoom(max_players=8)
            room.add_player_at(my_id, 0, is_ai=False)
            for i, name in enumerate(AI_NAMES, start=1):
                room.add_player_at(name, i, is_ai=True)
            server_state.solo_rooms[my_id] = room
        room = server_state.solo_rooms[my_id]

        # 新一轮：如果没开始且不在结算，就开新一轮
        if not room.game_active and room.stage != "showdown":
            room.start_new_hand()

        # 让 AI 自动行动
        process_ai_actions(room)

    col_a, col_b = st.columns([4, 1])
    with col_a:
        st.caption("🎯 单人练习模式（对方为模拟账户）")
    with col_b:
        if st.button("← 返回大厅", key="back_to_lobby"):
            st.session_state.mode = "multi"
            st.rerun()

# ---------- 多人模式 ----------
else:
    with server_state_lock["room"]:
        need_new = False
        if "room" not in server_state:
            need_new = True
        elif not hasattr(server_state.room, "is_seat_empty"):
            need_new = True
        if need_new:
            server_state.room = PokerRoom(max_players=8)
        room = server_state.room

        room.heartbeat(my_id)
        room.cleanup_stale(timeout=15)

    already_seated = room.has_player(my_id)

    if not already_seated:
        st.subheader("请选择你的席位")

        if room.is_full():
            st.warning("当前协作席位已满，请稍后再试。")
            st.stop()

        cols = st.columns(4)
        for i in range(8):
            with cols[i % 4]:
                if room.is_seat_empty(i):
                    if st.button(f"席位 {i+1}", key=f"seat_{i}"):
                        with server_state_lock["room"]:
                            room.add_player_at(my_id, i)
                        st.rerun()
                else:
                    seat = room.seats[i]
                    st.button(f"席位 {i+1}（{seat.player_id}）", disabled=True, key=f"seat_{i}")

        st.divider()
        if st.button("🎯 单人练习", key="enter_solo"):
            st.session_state.mode = "solo"
            st.rerun()

        st.stop()

# ---------- 侧边栏 ----------
with st.sidebar:
    st.header("⚙️ 个人参数配置")

    me = room.get_player(my_id)
    if me:
        st.metric("我的标识", my_id)
        st.metric("当前余额", f"{me.chips:,}")

        hole = me.hole_cards
        if hole:
            st.write("**当前持有资源**")
            st.code(f"{hole[0]}  {hole[1]}")

        if room.is_my_turn(my_id) and room.game_active:
            st.write("**可执行操作**")

            col1, col2 = st.columns(2)

            with col1:
                if st.button("✅ 确认当前方案", key="action_call"):
                    room.player_check_or_call(my_id)
                    process_ai_actions(room)
                    st.rerun()

                if st.button("🔄 调整投入", key="action_raise_open"):
                    st.session_state.show_raise = True

                if st.session_state.get("show_raise"):
                    amount = st.number_input(
                        "调整数量", min_value=room.min_raise, value=room.min_raise,
                        step=room.blind_big, key="raise_amount"
                    )
                    if st.button("执行调整", key="action_raise_confirm"):
                        room.player_raise(my_id, int(amount))
                        st.session_state.show_raise = False
                        process_ai_actions(room)
                        st.rerun()

            with col2:
                if st.button("⏸️ 暂不参与本轮", key="action_fold"):
                    room.player_fold(my_id)
                    process_ai_actions(room)
                    st.rerun()

# ---------- 主区域 ----------
st.subheader("当前协作状态")

col_pot, col_stage = st.columns(2)
with col_pot:
    st.metric("当前资源池", f"{room.pot:,}")
with col_stage:
    stage_map = {"preflop": "阶段一", "flop": "阶段二", "turn": "阶段三", "river": "阶段四", "showdown": "结算中"}
    st.metric("当前阶段", stage_map.get(room.stage, room.stage))

if room.community_cards:
    st.write("**公共资源**")
    st.code("  ".join(room.community_cards))

st.write("**席位状态**")

table_data = []
for i in range(8):
    seat = room.seats[i]
    if seat is None:
        table_data.append({"席位": i + 1, "标识": "(空位)", "状态": "—", "余额": "—", "本轮动作": "—"})
    else:
        is_me = " ◀" if seat.player_id == my_id else ""
        status = "活跃" if seat.active else "已退出"
        if room.is_my_turn(seat.player_id) and room.game_active:
            status = "待操作"
        table_data.append({
            "席位": i + 1,
            "标识": seat.player_id + is_me,
            "状态": status,
            "余额": f"{seat.chips:,}",
            "本轮动作": seat.last_action or "—",
        })

st.dataframe(table_data, use_container_width=True, hide_index=True)

# 底部按钮
if mode == "multi":
    with server_state_lock["room"]:
        if not room.game_active and room.player_count() >= 2:
            if st.button("🚀 启动同步", key="start_multi"):
                room.start_new_hand()
                st.rerun()
        if room.game_active and room.stage == "showdown":
            st.info("本轮同步已完成。")
            if st.button("📋 开始下一轮", key="next_multi"):
                room.start_new_hand()
                st.rerun()
else:
    if room.game_active and room.stage == "showdown":
        st.info("本轮同步已完成。")
        if st.button("📋 开始下一轮", key="next_solo"):
            room.start_new_hand()
            process_ai_actions(room)
            st.rerun()
