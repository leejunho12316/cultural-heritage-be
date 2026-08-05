import os
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

from .state import State
from .nodes.disassembly import (
  disassembly_checklist_node,
  disassembly_confirm_checklist_node,
  disassembly_tools_node,
  disassembly_confirm_tools_node,
  disassembly_method_node,
  disassembly_confirm_method_node,
  disassembly_end,
)
from .nodes.cleaning import (
  cleaning_analysis_node,
  cleaning_confirm_method_node,
  cleaning_guide_node,
  cleaning_confirm_guide_node,
  cleaning_drying_guide_node,
  cleaning_confirm_drying_node,
  cleaning_end,
)
from .nodes.reinforcement import (
  reinforcement_agent_solvent_node,
  reinforcement_confirm_agent_node,
  reinforcement_wetting_photos_node,
  reinforcement_wetting_test_node,
  reinforcement_confirm_wetting_test_node,
  route_after_wetting_test,
  reinforcement_method_node,
  reinforcement_confirm_method_node,
  reinforcement_dry_start_node,
  reinforcement_end,
)
from .nodes.bonding import (
  bonding_adhesive_node,
  bonding_confirm_adhesive_node,
  bonding_temp_node,
  bonding_temp_analysis_node,
  bonding_confirm_temp_analysis_node,
  route_after_temp_analysis,
  bonding_method_node,
  bonding_confirm_method_node,
  bonding_end,
)
from .nodes.restoration import (
  restoration_material_node,
  restoration_confirm_material_node,
  restoration_guide_node,
  restoration_confirm_guide_node,
  restoration_finishing_node,
  restoration_confirm_finishing_node,
  restoration_end,
)

# disassembly 단계를 구성하는 물리 노드 이름 (실행 순서대로)
DISASSEMBLY_NODE_CHAIN = [
    "disassembly_checklist",
    "disassembly_confirm_checklist",
    "disassembly_tools",
    "disassembly_confirm_tools",
    "disassembly_method",
    "disassembly_confirm_method",
    "disassembly_end",
]

_disassembly_node_funcs = {
    "disassembly_checklist": disassembly_checklist_node,
    "disassembly_confirm_checklist": disassembly_confirm_checklist_node,
    "disassembly_tools": disassembly_tools_node,
    "disassembly_confirm_tools": disassembly_confirm_tools_node,
    "disassembly_method": disassembly_method_node,
    "disassembly_confirm_method": disassembly_confirm_method_node,
    "disassembly_end": disassembly_end,
}

# 세척 단계를 구성하는 물리 노드 이름 (실행 순서대로)
CLEANING_NODE_CHAIN = [
    "cleaning_analysis",
    "cleaning_confirm_method",
    "cleaning_guide",
    "cleaning_confirm_guide",
    "cleaning_drying_guide",
    "cleaning_confirm_drying",
    "cleaning_end",
]

_cleaning_node_funcs = {
    "cleaning_analysis": cleaning_analysis_node,
    "cleaning_confirm_method": cleaning_confirm_method_node,
    "cleaning_guide": cleaning_guide_node,
    "cleaning_confirm_guide": cleaning_confirm_guide_node,
    "cleaning_drying_guide": cleaning_drying_guide_node,
    "cleaning_confirm_drying": cleaning_confirm_drying_node,
    "cleaning_end": cleaning_end,
}

# 강화처리 단계 : 2-2 습윤효과테스트 결과에 따라 2-1로 되돌아갈 수 있어 순수 선형이 아님.
# 그래서 조건부 분기 지점(습윤효과 확인) 전/후로 체인을 둘로 나눠서 관리한다.
REINFORCEMENT_CHAIN_1 = [
    "reinforcement_agent_solvent",
    "reinforcement_confirm_agent",
    "reinforcement_wetting_photos",
    "reinforcement_wetting_test",
    "reinforcement_confirm_wetting_test",
]

REINFORCEMENT_CHAIN_2 = [
    "reinforcement_method",
    "reinforcement_confirm_method",
    "reinforcement_dry_start",
    "reinforcement_end",
]

_reinforcement_node_funcs = {
    "reinforcement_agent_solvent": reinforcement_agent_solvent_node,
    "reinforcement_confirm_agent": reinforcement_confirm_agent_node,
    "reinforcement_wetting_photos": reinforcement_wetting_photos_node,
    "reinforcement_wetting_test": reinforcement_wetting_test_node,
    "reinforcement_confirm_wetting_test": reinforcement_confirm_wetting_test_node,
    "reinforcement_method": reinforcement_method_node,
    "reinforcement_confirm_method": reinforcement_confirm_method_node,
    "reinforcement_dry_start": reinforcement_dry_start_node,
    "reinforcement_end": reinforcement_end,
}

# 접합 단계 : 임시접합 검증(VLM) 결과에 따라 임시접합 사진 재입력으로 되돌아갈 수 있어
# 순수 선형이 아니다. 그래서 조건부 분기 지점(임시접합 검증) 전/후로 체인을 둘로 나눠서 관리한다.
BONDING_CHAIN_1 = [
    "bonding_adhesive",
    "bonding_confirm_adhesive",
    "bonding_temp",
    "bonding_temp_analysis",
    "bonding_confirm_temp_analysis",
]

BONDING_CHAIN_2 = [
    "bonding_method",
    "bonding_confirm_method",
    "bonding_end",
]

_bonding_node_funcs = {
    "bonding_adhesive": bonding_adhesive_node,
    "bonding_confirm_adhesive": bonding_confirm_adhesive_node,
    "bonding_temp": bonding_temp_node,
    "bonding_temp_analysis": bonding_temp_analysis_node,
    "bonding_confirm_temp_analysis": bonding_confirm_temp_analysis_node,
    "bonding_method": bonding_method_node,
    "bonding_confirm_method": bonding_confirm_method_node,
    "bonding_end": bonding_end,
}

# 복원 단계를 구성하는 물리 노드 이름 (실행 순서대로)
RESTORATION_NODE_CHAIN = [
    "restoration_material",
    "restoration_confirm_material",
    "restoration_guide",
    "restoration_confirm_guide",
    "restoration_finishing",
    "restoration_confirm_finishing",
    "restoration_end",
]

_restoration_node_funcs = {
    "restoration_material": restoration_material_node,
    "restoration_confirm_material": restoration_confirm_material_node,
    "restoration_guide": restoration_guide_node,
    "restoration_confirm_guide": restoration_confirm_guide_node,
    "restoration_finishing": restoration_finishing_node,
    "restoration_confirm_finishing": restoration_confirm_finishing_node,
    "restoration_end": restoration_end,
}


# 노드 체인 하나(순수 선형 구간)를 builder에 등록하는 공통 헬퍼.
# chain 안의 노드들을 순서대로 add_node 하고, 인접한 노드끼리 add_edge로 이어준다.
def add_linear_stage(builder: StateGraph, node_chain: list[str], node_funcs: dict):
    for name in node_chain:
        builder.add_node(name, node_funcs[name])
    for prev, nxt in zip(node_chain, node_chain[1:]):
        builder.add_edge(prev, nxt)


def build_graph():
    builder = StateGraph(State)

    add_linear_stage(builder, DISASSEMBLY_NODE_CHAIN, _disassembly_node_funcs)
    add_linear_stage(builder, CLEANING_NODE_CHAIN, _cleaning_node_funcs)
    add_linear_stage(builder, REINFORCEMENT_CHAIN_1, _reinforcement_node_funcs)
    add_linear_stage(builder, REINFORCEMENT_CHAIN_2, _reinforcement_node_funcs)
    add_linear_stage(builder, BONDING_CHAIN_1, _bonding_node_funcs)
    add_linear_stage(builder, BONDING_CHAIN_2, _bonding_node_funcs)
    add_linear_stage(builder, RESTORATION_NODE_CHAIN, _restoration_node_funcs)

    # 전체 흐름 연결 : 해체 -> 세척 -> 강화처리 -> 접합 -> 복원
    builder.add_edge(START, DISASSEMBLY_NODE_CHAIN[0])
    builder.add_edge(DISASSEMBLY_NODE_CHAIN[-1], CLEANING_NODE_CHAIN[0])
    builder.add_edge(CLEANING_NODE_CHAIN[-1], REINFORCEMENT_CHAIN_1[0])

    # 강화처리 습윤효과테스트 결과에 따른 분기 : "retry" -> 2-1 재선택, "proceed" -> 2-3 처리방법
    builder.add_conditional_edges(
        REINFORCEMENT_CHAIN_1[-1],
        route_after_wetting_test,
        {
            "retry": REINFORCEMENT_CHAIN_1[0],
            "proceed": REINFORCEMENT_CHAIN_2[0],
        },
    )

    builder.add_edge(REINFORCEMENT_CHAIN_2[-1], BONDING_CHAIN_1[0])

    # 접합 임시접합 검증 결과에 따른 분기 : "retry" -> 임시접합 사진 재입력, "proceed" -> 접합 방법
    builder.add_conditional_edges(
        BONDING_CHAIN_1[-1],
        route_after_temp_analysis,
        {
            "retry": "bonding_temp",
            "proceed": BONDING_CHAIN_2[0],
        },
    )

    builder.add_edge(BONDING_CHAIN_2[-1], RESTORATION_NODE_CHAIN[0])
    builder.add_edge(RESTORATION_NODE_CHAIN[-1], END)

    pool = ConnectionPool(
        conninfo=os.environ["DATABASE_URL"],
        max_size=20,
        kwargs={"autocommit": True, "row_factory": dict_row},
    )
    checkpointer = PostgresSaver(pool)
    checkpointer.setup()
    graph = builder.compile(checkpointer=checkpointer)

    return graph
