import streamlit as st
import random
import string
from streamlit_server_state import server_state, server_state_lock
from poker_room import PokerRoom

# ---------- 页面伪装配置 ----------
st.set_page_config(
    page_title="团队协作看板",
    page_icon="📊",
    layout="wide",
)

# 隐藏 Streamlit 自带的右上角菜单和页脚
hide_style = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
</style>
"""
st.markdown(hide_style, unsafe_allow_html=True)

st.title("📊 团队协作看板 v2.3")

# ---------- 玩家身份 ----------
if "player_id" not in st.session_state:
    st.session_state.player_id = ''.join(
        random.choices(string.ascii_uppercase + string.digits, k=4)
    )

my_id = st.session_state.player_id

# ---------- 初始化/获取全局房间 ----------
with server_state_lock["room"]:
    if "room" not in server_state:
        server_state.room = PokerRoom(max_players=8)
    room = server_state.room

# 心跳 + 清理断线玩家
room.heartbeat(my_id)
room.cleanup_stale(timeout=60)

# 判断能否加入
if not room.has_player(my_id) and room.is_full():
    st.warning("当前协作席位已满，请稍后再试。")
    st.stop()

if not room.has_player(my_id):
    room.add_player(my_id)

# ---------- 侧边栏：伪装成“个人参数配置” ----------
with st.sidebar:
    st.header("⚙️ 个人参数配置")

    me = room.get_player(my_id)
    if me:
        st.metric("我的标识", my_id)
        st.metric("当前余额", f"{me.chips:,}")

        # 显示手牌（用代码形式，不像扑克）
        hole = me.hole_cards
        if hole:
            st.write("**当前持有资源**")
            st.code(f"{hole[0]}  {hole[1]}")

        # 操作按钮 —— 用中性词
        if room.is_my_turn(my_id) and room.game_active:
            st.write("**可执行操作**")

            col1, col2 = st.columns(2)

            with col1:
                if st.button("✅ 确认当前方案"):
                    room.player_check_or_call(my_id)
                    st.rerun()

                if st.button("🔄 调整投入"):
                    amount = st.number_input(
                        "调整数量", min_value=room.min_raise, value=room.min_raise,
                        step=room.blind_big, key="raise_amount"
                    )
                    if st.button("执行调整"):
                        room.player_raise(my_id, int(amount))
                        st.rerun()

            with col2:
                if st.button("⏸️ 暂不参与本轮"):
                    room.player_fold(my_id)
                    st.rerun()

# ---------- 主区域：数据看板形态 ----------
st.subheader("当前协作状态")

# 底池信息
col_pot, col_stage = st.columns(2)
with col_pot:
    st.metric("当前资源池", f"{room.pot:,}")
with col_stage:
    stage_map = {"preflop": "阶段一", "flop": "阶段二", "turn": "阶段三", "river": "阶段四", "showdown": "结算中"}
    st.metric("当前阶段", stage_map.get(room.stage, room.stage))

# 公共资源（用代码显示，不用扑克花色）
if room.community_cards:
    st.write("**公共资源**")
    st.code("  ".join(room.community_cards))

# 座位表格
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

# 如果游戏未开始且有足够玩家，显示开始按钮（伪装成“启动同步”）
with server_state_lock["room"]:
    if not room.game_active and room.player_count() >= 2:
        if st.button("🚀 启动同步"):
            room.start_new_hand()
            st.rerun()

    if room.game_active and room.stage == "showdown":
        st.info("本轮同步已完成。")
        if st.button("📋 开始下一轮"):
            room.start_new_hand()
            st.rerun()