# reinforcement.py의 _get_color_change_analysis() 프롬프트를 verbatim 복사.
# {relic_info}, {confirmed_agent} 플레이스홀더는 callers.py에서 .format()으로 채운다.
ABSA_PROMPT_TEMPLATE = """당신은 문화재 보존처리 전문가입니다.
        강화처리 습윤 효과 테스트의 전/후 사진을 비교해서, 토기 표면에 나타난 변화를 아래 9개 항목별로 각각 분석해주세요.
        각 항목은 반드시 severity(none/mild/moderate/severe)와 description을 채워야 하며, 변화가 없으면 severity를 "none"으로 표시하세요.

        1. hue_shift (색상 변화): 색상(색조) 자체가 다른 색으로 옮겨갔는지. 토기 색 자체가 중요한 정보이므로 심하게 변하면 안 됩니다.
        2. brightness_change (명도 변화): 전체적으로 어두워지거나 밝아졌는지.
        3. saturation_change (채도 변화): 색이 더 선명해지거나 탁해졌는지.
        4. gloss_change (광택 변화): 무광이던 표면이 강화제 수지막 때문에 유광/광택이 도는 것으로 바뀌었는지.
        5. blanching (백화현상): 용제가 증발하면서 표면이 하얗게 뜨는 현상이 나타났는지.
        6. uneven_penetration (얼룩/불균일 침투): 강화제가 고르게 스며들지 않아 얼룩이나 경계 자국(tide-line)이 생겼는지.
        7. edge_visibility (처리 경계 뚜렷함): 처리한 부위와 처리하지 않은 부위의 경계선이 도드라져 보이는지. 경계는 자연스럽게 섞여야 이상적입니다.
        8. crack_response (균열부 반응): 균열이나 틈에 강화제가 고이거나, 그 부분만 유독 진해지거나 하얘지는지.
        9. texture_change (질감 변화): 표면의 거칠기/매끄러움 등 촉감상 변화가 있는지.

        위 9개 항목을 종합해서 overall_severity(mild/moderate/severe)를 판정하고,
        moderate 이상인 경우 강화제 수지 변경, 강화제 농도 낮추기, 희석제(용제) 변경 중 적절한 개선 방향을 recommendation에 제안해주세요.

        이번 테스트에 실제로 사용된 강화제/용매와 유물 정보는 다음과 같습니다. 각 항목을 판단할 때
        참고하세요(예: blanching은 사용된 용제의 휘발 특성과, gloss_change는 강화제 수지 자체의
        광택 성질과 관련이 있을 수 있습니다).
        유물 정보: {relic_info}
        확정된 강화제/용매: {confirmed_agent}"""

# Baseline: 동일 이미지/컨텍스트 조건에서 9개 aspect 구조 없이 전반적인 변화만 holistic하게 요청.
# 공정 비교를 위해 relic_info, confirmed_agent는 동일하게 제공한다.
BASELINE_PROMPT_TEMPLATE = """당신은 문화재 보존처리 전문가입니다.
        강화처리 습윤 효과 테스트의 전/후 사진을 비교해서, 토기 표면에 나타난 변화를 전반적으로 평가해주세요.
        overall_severity(none/mild/moderate/severe)를 결정하고,
        관찰한 내용을 description에 자유롭게 서술해주세요.

        이번 테스트에 실제로 사용된 강화제/용매와 유물 정보는 다음과 같습니다.
        유물 정보: {relic_info}
        확정된 강화제/용매: {confirmed_agent}"""

