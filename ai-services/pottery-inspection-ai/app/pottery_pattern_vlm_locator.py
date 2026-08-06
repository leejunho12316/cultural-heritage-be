"""
pottery_pattern_vlm_locator.py

도자기 사진을 VLM으로 여러 번 분석하고, 서로 비슷한 위치의 결과를
호출 간 합의 결과로 병합한다.

핵심 변경점 (1차)
- 대형 연속 문양을 머리/몸통/꼬리처럼 쪼개지 않도록 프롬프트 보강
- 문양 범위(pattern_scope)와 대략적 bbox를 함께 요청
- 같은 호출 안에서 나온 가까운 항목은 합의 횟수를 늘리지 않음
- 위치 합의와 이름 합의를 별도 수치로 저장
- 첫 번째 호출 문장을 그대로 쓰지 않고 합의 결과로 요약 생성
- pattern_name에 "상단띠"/"하단띠"처럼 위치를 가리키는 일반 명사 대신
  실제 전통 문양명(구름문/당초문 등)을 쓰도록 프롬프트에 명시

개선 사항 (2차, 코드 리뷰 반영)
- VLM에게 실제로 요청하는 스키마(PatternLocationRaw/PotteryPatternAnalysisRaw)와
  ensemble 이후에만 채워지는 필드(PatternLocation/PotteryPatternAnalysis)를
  분리했다. 기존에는 agreement_count 같은 후처리 필드까지 모델에게 그대로
  요청 스키마로 보내고 있었는데, 이는 구조화 출력 안정성을 떨어뜨리고
  모델이 채울 필요 없는 필드까지 신경 쓰게 만드는 문제가 있었다.
- 수동으로 model_json_schema()를 만들어 response_format에 넣는 대신
  client.beta.chat.completions.parse(response_format=PydanticModel)을
  사용한다. SDK가 strict 모드에 맞는 스키마 변환을 대신 해주므로 더 안전하다.
  (openai 파이썬 SDK 1.40 이상 필요)
- 여러 번의 VLM 호출이 순차 for문이었던 것을 ThreadPoolExecutor로
  병렬화했다. 서로 독립적인 API 호출이라 병렬화해도 결과에 영향이 없고,
  n_calls가 커질수록 체감 대기 시간이 크게 줄어든다.
- 클러스터링 거리 계산이 x_percent/y_percent를 정사각형 이미지 기준처럼
  그대로 유클리드 거리로 썼던 문제를 고쳤다. 가로세로 비율이 다른 사진에서는
  실제 픽셀 거리로 환산한 뒤 다시 정규화하는 방식으로 왜곡을 없앴다.
- min_agreement(기본 2)가 n_calls보다 큰 경우(예: n_calls=1) 어떤 항목도
  합의 기준을 못 넘겨 기본 화면이 항상 비어버리는 문제가 있었다. 실제 성공한
  호출 수 기준으로 유효 합의 기준(min_agreement_used)을 자동으로 낮춘다.
- 합의된 문양명이 참고 문양 목록(KNOWN_PATTERN_HINTS)에 없을 경우
  name_in_reference_list=False로 표시한다. 합의(agreement)는 여러 호출이
  같은 답을 냈다는 재현성일 뿐 정확성을 보장하지 않으므로, 조사자가
  낯선 명칭을 더 주의 깊게 검토할 수 있도록 신호를 추가했다.

개선 사항 (3차, 형태학적 판별 기준 도입)
- 기존에는 "이 이름 목록 중에 골라라"는 식으로만 프롬프트를 줬는데, 이건
  ensemble 합의(agreement)가 재현성만 측정하고 정확성은 측정하지 못한다는
  근본 문제를 해결하지 못했다 (여러 번 호출해도 모델이 매번 같은 착각을
  반복하면 합의율은 높게 나온다). 이를 보완하기 위해 문양별로
  필수에 가까운 특징/보조 특징/혼동 대상/판정 보류 조건을 명시한
  PATTERN_CRITERIA를 프롬프트에 포함시키고, VLM이 그 근거(visible_evidence/
  missing_evidence)와 판정 단계(decision: 확정/추정/판정보류)를 함께
  답하도록 스키마를 확장했다.
- pattern_name(기존 필드, 하위 호환 유지)은 그대로 "가장 가능성 높은
  후보명"으로 쓰고, 그 다음으로 가능성 있는 혼동 후보를 alternative_candidate에
  별도로 받는다. 어느 동식물/문양 대분류인지(pattern_family)도 함께 받아
  향후 필터링/검수에 쓸 수 있게 했다.
- KNOWN_PATTERN_HINTS 중 기준이 없던 5개(국화문/덩굴문/학문(두루미)/
  봉황문/인동문)의 기준을 추가했다. 조사 결과 덩굴문은 독립 문양이라기보다
  당초문의 하위개념이자 인동문을 가리키는 표현에 가까워, 별도 기준 대신
  인동문 기준을 함께 쓰도록 안내만 추가했다.

개선 사항 (4차, 다른 LLM 리뷰 + 실사용 피드백 반영)
- border_band가 상단·하단처럼 멀리 떨어진 두 위치를 한 항목에 합쳐서
  반환하는 경우가 있어, 프롬프트 지시만으로는 부족했다. _sanitize_raw_patterns()
  에서 location_description에 상단/하단 단어가 동시에 있으면 두 항목으로
  강제 분리하는 후처리를 추가했다 (방어적 이중 장치).
- small_instance인데 bbox가 사진의 큰 비율을 차지하면(가로/세로 30% 초과
  또는 면적 8% 초과) bbox를 버리고 decision을 판정보류로 낮춘다 - "작은
  개별 문양"이라는 분류와 "유물 절반을 덮는 bbox"가 데이터 안에서 모순되는
  문제가 있었다.
- alternative_candidate가 실제 None이 아니라 문자열 "null"/"None"/""/"없음"
  으로 들어오는 경우가 있어 이를 실제 None으로 정규화한다.
- pattern_family에 "자연문"을 추가하고 구름문을 "기타"에서 "자연문"으로
  옮겼다. 구름은 자연현상을 소재로 한 문양이라 "기타"보다 이쪽이 더 맞다.

개선 사항 (5차, 실사용 보고서 품질 문제 발견 후)
- uncertainty 필드가 n_calls(기본 3)회 호출의 우려 문장을 그대로 이어붙이는
  방식이라, 실제로는 같은 내용("인동문/당초문 구분 어려움")을 호출마다
  조금씩 다른 문장으로 표현한 경우에도 3개가 전부 남아 보고서가 장황하고
  중복돼 보이는 문제가 실사용 사진에서 확인됐다. 문장 전체의 문자 유사도로
  판단하면 표현이 달라 유사도가 낮게 나와 걸러지지 않았다(단순 자카드
  유사도로는 0.2~0.25 수준). 대신 각 우려 문장이 KNOWN_PATTERN_HINTS 중
  어떤 문양명들을 함께 언급하는지(예: {"인동문","당초문"})로 "의미적
  서명"을 만들어, 같은 서명을 가진 문장들을 하나로 묶고 그중 가장 정보가
  많은(긴) 문장을 대표로 남기며 "(유사 소견 N건 통합)"을 덧붙인다.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import io
import json
import os
from collections import Counter, defaultdict
from typing import Literal

from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image
from pydantic import BaseModel, Field

load_dotenv()

MODEL_NAME = "gpt-5.6-terra"
DEFAULT_N_CALLS = 3
DEFAULT_MIN_AGREEMENT = 2
SMALL_CLUSTER_DISTANCE = 6.0
LARGE_CLUSTER_DISTANCE = 12.0
BORDER_CLUSTER_DISTANCE = 10.0
MAX_PARALLEL_CALLS = 5
# border_band 하나(진짜 "띠" 한 줄)의 세로 폭이 사진 세로 길이의 이 비율(%)을
# 넘으면, 서로 다른 띠 여러 개가 한 항목으로 합쳐졌을 가능성이 높다고 보고
# _sanitize_raw_patterns()에서 절반으로 강제 분리한다.
BORDER_BAND_MAX_HEIGHT_PERCENT = 20.0

KNOWN_PATTERN_HINTS = [
    "연꽃문",
    "연화문",
    "연판문",
    "모란문",
    "매화문",
    "대나무문",
    "당초문",
    "국화문",
    "덩굴문",
    "구름문",
    "학문(두루미)",
    "용문",
    "봉황문",
    "어문",
    "인동문",
    "여의두문",
    "뇌문",
    "만자문",
    "파형문",
    "팔괘문",
    "태극문",
]

# pattern_name에 이런 위치/부위 서술어가 그대로 들어오면 안 된다는 걸
# 프롬프트에 명시하기 위한 예시 목록 (검증 코드가 아니라 프롬프트 문구용).
FORBIDDEN_NAME_EXAMPLES = [
    "상단띠", "하단띠", "몸통무늬", "구연부무늬", "굽무늬", "어깨무늬", "목띠",
]

# 문양 대분류. pattern_family 필드에 그대로 쓰인다. VLM 스키마 검증용이
# 아니라 프롬프트에서 "이 문양은 어느 대분류인가"를 먼저 좁혀 생각하게
# 만들기 위한 참고 정보다. 구름문은 동식물이 아니라 자연현상(구름)을
# 소재로 하므로 "기타"보다 "자연문"이 더 정확하다는 피드백을 반영했다.
# "기하문"은 주미경(2014, 「한국 도자기 문양의 특성과 상징성 연구」)이 다룬
# 뇌문/만자문/파형문/팔괘문/태극문처럼 특정 동식물이 아니라 반복되는 선·도형
# 자체가 상징인 문양을 위한 대분류다.
PATTERN_FAMILY_MAP: dict[str, str] = {
    "용문": "동물문",
    "어문": "동물문",
    "봉황문": "동물문",
    "학문(두루미)": "동물문",
    "모란문": "식물문",
    "매화문": "식물문",
    "대나무문": "식물문",
    "연화문": "식물문",
    "연꽃문": "식물문",
    "연판문": "식물문",
    "당초문": "식물문",
    "국화문": "식물문",
    "인동문": "식물문",
    "덩굴문": "식물문",
    "구름문": "자연문",
    "여의두문": "기타",
    "뇌문": "기하문",
    "만자문": "기하문",
    "파형문": "기하문",
    "팔괘문": "기하문",
    "태극문": "기하문",
}

# 문양별 판별 기준. 사용자가 국립문화유산연구원/문화포털/한국민족문화대백과사전을
# 참고해 정리한 6개(용문/운룡문, 모란문, 매화문, 연화문/연판문, 당초문, 구름문,
# 여의두문) 기준과, 그 형식을 따라 이번에 추가한 5개(국화문, 덩굴문, 학문(두루미),
# 봉황문, 인동문) 기준을 모두 담는다. "필수에 가까운 특징"이라고 완곡하게 쓴 건
# 사진 각도/마모/유약 상태에 따라 일부 특징이 안 보일 수 있어 기계적 필요조건으로
# 못박기는 어렵기 때문이다. 이 경우엔 missing_evidence에 적고 decision을
# 낮추도록 프롬프트에서 안내한다.
#
# 아래 5개(뇌문, 만자문, 파형문, 팔괘문, 태극문)는 사용자가 제공한 주미경(2014),
# 「한국 도자기 문양의 특성과 상징성 연구 -기하형 문양을 중심으로-」를 반영해
# 추가했다. 이 5개는 기존 목록에 전혀 없던 기하학적 문양으로, 없으면 VLM이
# 이런 문양을 마주쳤을 때 근거 없이 다른 이름을 붙이거나(예: 뇌문을 당초문으로
# 오인) "목록 외 명칭"으로만 처리할 수밖에 없었다.
#
# 모란문/국화문/여의두문/당초문(연화당초문) 항목에는 조원교(2019), 「고려·조선
# 시대 도자기의 蓮華文 연구」의 논지를 "학계 참고" 절로 추가했다. 이 논문은
# 고려·조선시대 도자기에서 모란문·국화문·보상화문·여의두문·(연화)당초문으로
# 불리는 문양의 상당수가 실은 연화문(연꽃문)이 기·화생(생명력 표현)을 거치며
# 원(原)연화문→첫연화문→중간연화문→덩굴연화문 단계로 변형된 모습이며, 꽃잎과
# 잎사귀가 시각적으로 분리되면서 모란·국화 등으로 오인됐다는 논지를 편다(이는
# 학계 일부의 재해석이며 보편적으로 합의된 분류는 아니다). 이 논문 자체가
# 제시하는 핵심 판별 팁 - 같은 유물의 다른 부분에 자방(둥근 씨방)·연꽃봉오리·
# 좌우 대칭 화생 구도처럼 더 명백한 연화문 요소가 함께 있는지 확인하라 - 은
# 실제로 판별에 유용하므로 각 항목의 "혼동 대상"에 반영했다. 다만 이 도구는
# 관행적으로 통용되는 명칭(모란문/국화문 등)을 그대로 pattern_name 후보로
# 유지하며, 근거가 애매할 때만 alternative_candidate로 연화문 계열을 함께
# 제시하도록 안내한다 - 논쟁적인 단일 학설을 이유로 관행 명칭을 임의로
# 폐기하지 않기 위함이다.
PATTERN_CRITERIA: dict[str, str] = {
    "용문": """
[용문/운룡문] (동물문)
정의: 상상의 동물인 용을 소재로 한 문양. 왕권·권위·상서로움을 상징.
필수에 가까운 특징: 비늘로 덮인 긴 몸통이 S자형으로 구불거림 / 뿔과 갈기, 날카로운
발톱이 있는 다리(보통 3~5개 발가락) / 크게 벌린 입과 수염
보조 특징: 여의주를 희롱하는 구도 / 몸통 주변을 감싸는 구름문과 함께 등장 / 몸
전체가 부분적으로 끊어 보이지 않고 하나로 길게 이어짐
혼동 대상: 이무기·뱀 문양(뿔·발톱이 없으면 용이 아닌 다른 동물로 검토) / 구름문
(용의 몸통 일부만 보이고 나머지가 구름에 가려진 경우)
판정 보류: 몸통 일부만 보이고 머리·발톱이 화면 밖이거나 가려짐 / 비늘·뿔 여부
확인 불가
""".strip(),
    "모란문": """
[모란문] (식물문)
정의: 모란(牡丹)을 소재로 한 장식무늬. 부귀영화를 상징.
필수에 가까운 특징: 꽃잎이 넓고 둥글며 여러 겹으로 겹쳐 풍성한 덩어리를 이룸 /
꽃잎 가장자리가 물결치듯 부드럽게 굴곡짐
보조 특징: 크고 넓은 잎(결각이 깊지 않음)과 함께 등장 / 줄기가 굵고 완만하게
휘어짐 / 꽃이 좌우 비대칭으로 풍성하게 벌어진 형태
혼동 대상: 국화문(꽃잎이 가늘고 길며 방사형 배열이 뚜렷한 경우가 많음) / 연화문
(꽃잎이 더 길쭉하고 뾰족하며 연잎·연밥이 동반되면 연화 우선)
학계 참고(조원교, 2019): 고려·조선시대 도자기의 "모란문"으로 불리는 문양 상당수는
연꽃잎과 잎사귀가 크고 넓게 분리 표현되며 꽃나무처럼 보이게 된 연화문(덩굴
연화문)이라는 재해석이 있다. 판별 팁: 같은 유물의 다른 부분(예: 굽 언저리,
뚜껑, 문양 아래쪽 끝)에 자방(둥근 씨방)이나 연꽃봉오리, 연판(연꽃잎) 형태가
뚜렷이 함께 있다면 이 문양도 연화문 계열일 가능성을 alternative_candidate에
"연화문(덩굴 연화문 계열)"로 함께 적을 것. 다만 이는 하나의 학설이므로
pattern_name은 여전히 통용되는 "모란문"을 우선 사용하고, 근거가 애매하면
decision을 낮출 것.
판정 보류: 꽃잎 개수·겹침 정도가 뭉개져 안 보임 / 잎 형태 확인 불가
""".strip(),
    "매화문": """
[매화문] (식물문)
정의: 매화(梅花)를 소재로 한 장식무늬. 지조·절개, 이른 봄의 생명력을 상징.
필수에 가까운 특징: 꽃잎이 5장으로 둥글고 작으며 서로 겹치지 않는 홑꽃 형태 /
꽃이 마디가 많고 각진 노목(老木) 가지에 듬성듬성 붙어 있음
보조 특징: 꽃과 가지만 간결하게 그려지고 잎은 거의 생략됨 / 원 안에 점 하나
(꽃술)로 단순화된 표현 / 대나무·새와 함께 구성(매조문)
혼동 대상: 국화문·모란문(꽃잎이 겹겹이거나 가늘고 길면 매화가 아님) / 벚꽃 유사
문양(공식 분류 체계에서는 별도 표기 없이 매화문으로 포괄하는 경우가 많음)
판정 보류: 꽃잎 개수가 5장인지 확인 불가 / 가지 형태 없이 꽃만 단독으로 보임
""".strip(),
    "대나무문": """
[대나무문] (식물문)
정의: 대나무(竹)를 소재로 한 장식무늬. 사군자의 하나로 지조·절개·강인함을 상징.
필수에 가까운 특징: 마디가 뚜렷하게 구분되는 곧고 가는 줄기(마디마다 가로선/
띠 표시) / 줄기에서 좁고 긴 창(槍) 모양 잎이 2~5장씩 뭉쳐서 사선으로 뻗어나감
보조 특징: 잎 끝이 가늘고 뾰족하며 잎맥이 단순한 몇 개의 선으로만 표현 /
매화·새와 함께 구성(세한삼우·매조죽) / 줄기가 항아리 몸체를 따라 대각선으로
길게 이어짐
혼동 대상: 당초문/인동문(줄기가 매끈하게 휘어지며 마디가 없고, 잎이 부채꼴이나
넝쿨형이면 대나무가 아님 - 대나무 줄기는 곧고 마디가 뚜렷한 것이 핵심 차이) /
갈대·억새 등 다른 가늘고 긴 잎 식물(마디 유무로 구분)
판정 보류: 줄기의 마디가 선명하게 확인되지 않음 / 잎 다발의 개수·모양이
뭉개져 안 보임
""".strip(),
    "연화문": """
[연화문/연판문] (식물문, "연꽃문"과 동일 문양을 가리키는 표현)
정의: 연꽃을 소재로 한 장식무늬. 불교적 정결·재생을 상징. 꽃 전체를 그리면
연화문, 연꽃잎 하나하나를 반복 배치해 테두리처럼 두르면 연판문이라 부른다.
필수에 가까운 특징: 꽃잎 끝이 길쭉하고 뾰족하거나 완만한 반원형으로 정돈됨 /
꽃잎이 좌우 대칭으로 규칙적으로 반복 배치됨(연판문은 특히 규칙성이 강함)
보조 특징: 연잎(둥글고 큰 잎)이나 연밥(씨방)이 함께 등장 / 굽·구연부를 두르는
반복 띠 형태로 자주 사용(연판문) / 물 위에 뜬 구도로 표현
혼동 대상: 모란문(꽃잎이 둥글고 겹겹이 뭉치면 모란) / 국화문(꽃잎이 가늘고
방사형이면 국화) / 여의두문(연판문의 잎 끝 하나만 확대해서 보면 여의두문과
비슷해 보일 수 있음 - 반복되는 꽃잎 테두리 전체 구도인지로 구분)
학계 참고(조원교, 2019): 연화문은 한 유물 안에서도 기·화생(생명력 표현) 정도에
따라 여러 단계로 나타난다 - 자방(씨방)과 짧은 연꽃잎만 있는 "원(原)연화문",
1굽이 정도 자란 "첫 연화문", 2~3굽이로 자란 "중간 연화문", 3굽이 이상 길게
뻗은 "덩굴 연화문"(반복되면 "연속 반복 덩굴 연화문", 흔히 연화당초문·
인동당초문으로 불림). 같은 유물에서 여러 단계가 함께 보이면(예: 대접 안쪽
중앙의 간단한 연화문과 바깥쪽의 덩굴형 연화문) 모습이 달라도 같은 연화문
계열일 가능성이 높다는 뜻이므로 이를 근거로 활용할 것. 이는 하나의 학설이며
보편적 합의는 아니다.
판정 보류: 꽃잎 끝 형태가 마모·유약으로 뭉개짐 / 반복 배치인지 개별 꽃인지
불명확
""".strip(),
    "당초문": """
[당초문] (식물문)
정의: 식물 줄기가 곡선(S자)을 그리며 잎·꽃·열매를 감싸듯 뻗어나가는 덩굴무늬.
특정 식물 하나가 아니라 넝쿨의 생명력·번영을 상징하는 도안화된 문양.
필수에 가까운 특징: 줄기가 연속된 S자·소용돌이형 곡선으로 끊임없이 이어짐 /
줄기를 따라 잎이나 꽃이 일정 간격으로 반복됨
보조 특징: 좁고 긴 여백(구연부 아래, 굽 위 등)을 채우는 띠 형태로 자주 사용 /
잎 모양이 특정 식물로 특정되지 않고 도안화되어 있음
혼동 대상: 인동문/덩굴문(잎이 부채꼴로 뚜렷하게 갈라지면 인동문으로 세분) /
여의두문(줄기 없이 독립된 단위 문양이면 당초문이 아님)
학계 참고(조원교, 2019): 흔히 "연화당초문"이라 불리는 문양(연꽃과 당초문 두
문양이 결합됐다고 보는 통설)은 실은 연화문이 기·화생을 거쳐 길게 자란 "덩굴
연화문" 한 가지 문양이라는 재해석이 있다. 판별 팁: 줄기를 따라 반복되는
단위가 도안화된 잎이 아니라 연꽃봉오리·연꽃잎(끝이 길쭉하고 뾰족한 형태)에
가깝다면 "당초문"보다 "연화문(덩굴 연화문 계열)"을 alternative_candidate로
함께 제시할 것. 이는 하나의 학설이므로 pattern_name은 통용되는 "당초문"을
우선 사용할 것.
판정 보류: 줄기의 연속성 여부가 화면 밖으로 잘려 확인 불가 / 잎 모양이 뭉개져
인동문과의 구분 불가 → "당초문(하위유형 불확실)"
""".strip(),
    "국화문": """
[국화문] (식물문)
정의: 국화를 소재로 한 장식무늬. 사군자의 하나로 절개·지조를 상징. 도장을
찍듯 반복하는 인화(印花) 기법으로도 자주 표현된다.
필수에 가까운 특징: 가늘고 긴 꽃잎이 다수 겹으로 방사형 배열 / 꽃 전체가
원형·방사대칭에 가까운 실루엣 / 꽃잎 끝이 가늘고 뾰족함(모란처럼 넓고
둥글지 않음)
보조 특징: 톱니 모양(결각)이 있는 잎 / 여러 송이가 화면에 반복 배치됨 /
분청사기 인화문처럼 작은 도장 무늬로 촘촘히 반복되는 형태로도 등장
혼동 대상: 모란문(꽃잎이 넓고 둥글게 뭉친 느낌이면 모란 우선 검토) / 연화문
(꽃잎이 두툼하고 연잎·연밥이 동반되면 연화 우선) / 여의두문·당초문의
소용돌이형 잎 끝을 국화로 오인 가능
학계 참고(조원교, 2019): "국화문"으로 불리는 문양 상당수는 중심의 자방(둥근
씨방)에 비해 연꽃잎(연판)을 가늘고 길게 표현해 국화처럼 보이게 된 연화문
(주로 原연화문)이라는 재해석이 있다. 판별 팁: 꽃 중심에 둥근 씨방 흔적이
있거나, 같은 유물의 다른 위치에 더 명확한 연화문(연꽃봉오리·연잎)이 함께
있다면 alternative_candidate에 "연화문(原연화문 계열)"을 함께 적을 것. 이는
하나의 학설이므로 pattern_name은 통용되는 "국화문"을 우선 사용할 것.
판정 보류: 꽃잎 개별 형태가 안 보이고 뭉쳐진 덩어리로만 보임 / 방사대칭 여부
불명확
""".strip(),
    "인동문": """
[인동문/덩굴문] (식물문, 당초문의 하위 유형)
정의: 인동(忍冬)을 소재로 한 덩굴무늬. 겨울을 견디고도 시들지 않는 속성 때문에
장수·불변의 절개를 상징. 잎이 부채꼴로 펼쳐지는 것이 형식적 특징이며,
"덩굴문"이라는 표현은 대체로 이 인동문을 가리키는 경우가 많다.
필수에 가까운 특징: 연속되는 S자형 줄기(당초문과 공유) / 잎이 부채꼴·팔메트
모양으로 좌우 대칭 펼쳐짐 / 꽃보다 잎과 줄기의 반복이 두드러짐
보조 특징: 3~5갈래로 갈라진 부채꼴 잎끝 / 줄기 마디마다 반복되는 잎 배치 /
어깨·구연부를 두르는 띠 문양으로 자주 사용
혼동 대상: 당초문(잎 모양이 특정되지 않은 일반 덩굴이면 당초문, 부채꼴이
뚜렷하면 인동문으로 세분) / 여의두문(연속 줄기 없이 독립된 심(心)자형·고사리형
단위면 여의두문 우선)
판정 보류: 줄기는 보이나 잎이 뭉개져 부채꼴 여부 확인 불가 → "당초문(하위유형
불확실)"으로 낮춰서 답할 것
""".strip(),
    "구름문": """
[구름문] (자연문)
정의: 구름을 소재로 한 장식무늬. 상서로움, 신선 세계를 상징하며 용문·봉황문의
배경으로도 자주 함께 쓰인다.
필수에 가까운 특징: 뭉게뭉게 이어지는 곡선형 덩어리(소용돌이·리본 모양의
반복)가 서로 연결되며 퍼져나감 / 좌우 대칭보다는 자유로운 흐름형 실루엣
보조 특징: 용문·봉황문 주변을 감싸는 배경으로 등장 / 여러 개의 작은 뭉치가
흩어져 배치(운문)
혼동 대상: 당초문(구름은 줄기·잎 형태가 아니라 뭉치형 덩어리) / 용문의 몸통
일부(용 몸통과 구름이 겹치면 어디까지가 몸통이고 어디부터 구름인지 애매할 수
있음 - 비늘 여부로 구분)
판정 보류: 뭉치 형태가 용/봉황 몸통과 겹쳐 경계가 불명확 / 단순 여백/유약
얼룩과 구분 불가
""".strip(),
    "여의두문": """
[여의두문] (기타/장식문)
정의: 여의(불교 법구)의 머리 부분 모양을 본뜬 장식무늬. 원래 불교 장식에서
유래했으며 상서로움을 상징. 흔히 심장형(♡)이나 고사리순처럼 안으로 말린
독립 단위 문양으로 쓰인다.
필수에 가까운 특징: 하나의 독립된 단위 문양이 안쪽으로 둥글게 말린
심장형·고사리형 윤곽을 가짐 / 좌우 대칭에 가까운 형태
보조 특징: 띠 문양의 시작/끝이나 구획 경계에 장식적으로 배치 / 여러 개가 일정
간격으로 반복
혼동 대상: 인동문·당초문(연속된 줄기로 이어지면 여의두문이 아니라 당초/인동
계열) / 연판문(반복되는 꽃잎 테두리 전체 구도면 연판문 우선)
학계 참고(조원교, 2019): "여의두문"으로 불리는 문양은 실은 연꽃잎(蓮瓣) 표현
자체이며 여의(불교 법구)와는 모습만 우연히 닮았을 뿐이라는 재해석이 있다.
대체로 (1) 연꽃잎이 첫 연화문 형태로 변화하는 모습, (2) 세 연꽃봉오리(중앙과
좌우로 화생된 꽃)를 합친 윤곽 중 하나라고 본다. 판별 팁: 연화문과 같은 위치
(예: 같은 띠, 같은 단)에 반복 배치되어 있다면 alternative_candidate에
"연화문(연꽃봉오리 계열)"을 함께 적을 것. 이는 하나의 학설이므로 pattern_name은
통용되는 "여의두문"을 우선 사용할 것.
판정 보류: 독립 단위인지 연속 줄기의 일부인지 구분 불가
""".strip(),
    "학문(두루미)": """
[학문(두루미)] (동물문)
정의: 학(두루미)을 소재로 한 장식무늬. 장수를 상징.
필수에 가까운 특징: 길고 곧은 목과 다리 / 날씬하고 단순한 몸통 실루엣(깃털
세부묘사가 적음) / 몸통 대비 목·다리 비율이 김
보조 특징: 쌍으로 등장(쌍학문) / 소나무·구름·인물과 함께 구성 / 날개를 편
비상 자세 또는 목을 굽힌 정지 자세
혼동 대상: 봉황문(봉황은 볏과 화려한 다층 꼬리깃이 있고, 학은 실존 새의 사실적
비례로 꼬리가 짧고 수수함이 핵심 차이) / 기러기 등 다른 물새(목·다리 비율이
짧으면 학으로 보지 않음)
판정 보류: 새 실루엣만 있고 목·다리 비율 확인 불가 / 볏·꼬리깃 유무로 봉황과
구분 안 됨
""".strip(),
    "봉황문": """
[봉황문] (동물문)
정의: 상상의 새인 봉(수컷)과 황(암컷)을 함께 이르는 길상 문양.
필수에 가까운 특징: 머리에 볏(관모) 또는 화려한 머리깃 / 몸통보다 훨씬 길고
화려하게 뻗은 다층 꼬리깃(공작형) / 꿩·공작·학 등 여러 새의 특징이 섞인 듯한
상상적 실루엣
보조 특징: 오동나무·대나무와 함께 구성(오동봉황문) / 쌍으로 마주보는 구도 /
구름과 결합해 구름 위를 나는 구도
혼동 대상: 학문(꼬리 화려함 정도로 우선 구분 - 짧고 수수하면 학, 길고 화려하면
봉황) / 실제 공작을 사실적으로 그린 공작문(봉황은 여러 새가 섞인 상상적
형태라는 점에서 구분)
판정 보류: 몸통만 보이고 꼬리·볏이 잘려서 안 보임 / 학과 봉황을 가를 화려함
정도 판단 불가
""".strip(),
"어문": """
[어문] (동물문, 魚紋. 어해문·쌍어문 등도 이 계열)
정의: 물고기를 소재로 한 장식무늬. 다산·풍요·자유로움 등을 상징하며 청화백자 등에
자주 활용됨.
필수에 가까운 특징: 유선형 몸통에 비늘 표현(격자 또는 반원이 반복되는 무늬) / 몸통
양옆·꼬리 쪽에 부채꼴 또는 갈래로 뻗은 지느러미 / 다리·뿔 등 파충류·포유류적
요소 없이 매끈한 어형 실루엣
보조 특징: 물풀·수초·물결과 함께 구성(어조문/어해문) / 두 마리가 마주보거나
나란히 구성(쌍어문) / 몸체 중앙의 넓은 화면을 차지하는 주문양으로 등장
혼동 대상: 용문(물고기와 달리 뿔·수염·발톱 등 상상 속 파충류 특징이 있음 - 다리나
뿔이 없고 몸통이 매끈하면 어문) / 봉황문·학문(날개·깃털이 있는 새 형태와 지느러미가
있는 물고기 형태는 실루엣으로 명확히 구분됨)
판정 보류: 몸통 일부만 보여 지느러미·비늘 여부를 확인할 수 없음
""".strip(),
    "뇌문": """
[뇌문] (기하문, 回紋·뇌전문이라고도 함)
정의: 번개(우레)를 상징하는 기하문양. 직선이 직각으로 꺾이며 사각형 소용돌이를
이루는 모습이 반복됨. 길상·벽사의 의미를 가지며 주로 보조/종속 문양으로 쓰인다.
필수에 가까운 특징: 직각으로 꺾이는 사각형 소용돌이선(回자형) 또는 Z자형 꺾임이
일정 간격으로 촘촘히 반복됨 / 곡선이 아니라 각진 직선으로만 구성됨
보조 특징: 구연부·굽 언저리 등 좁은 띠 공간에 아주 작고 촘촘하게 반복 / 단색
음각·양각선으로만 표현되고 색 대비가 거의 없음 / 주문양이 아니라 테두리를
두르는 보조 문양으로 등장
혼동 대상: 당초문(뇌문은 부드러운 S자 곡선이 아니라 직각으로 꺾이는 사각형
반복이 핵심 - 곡선이면 당초문, 각진 사각 소용돌이면 뇌문) / 만자문·팔괘문
(각지고 반복되는 기하문이라는 공통점이 있으나, 卍자형이면 만자문, 3개씩 묶인
막대선이면 팔괘문, 사각 소용돌이면 뇌문으로 구분)
판정 보류: 문양이 너무 작거나 마모되어 직각 꺾임인지 곡선인지 구분 안 됨
""".strip(),
    "만자문": """
[만자문] (기하문, 卍字文)
정의: 불교의 卍(스와스티카) 기호를 소재로 한 길상문. 서양에서는 이 반복형을
palmette 계열과 별개로 분류하기도 한다.
필수에 가까운 특징: 卍 또는 卐 형태(십자가 네 끝이 모두 같은 방향으로 꺾인
모양)가 개별 단위로 뚜렷이 식별됨
보조 특징: 격자·바둑판 배경 위에 규칙적으로 반복 배치 / 연속으로 이어 붙여
띠 형태로 사용되기도 함 / 다른 길상 문양(박쥐문, 동전문 등)과 함께 구성
혼동 대상: 뇌문(만자가 연속으로 이어 붙으면 사각 소용돌이 모양인 뇌문과 비슷해
보일 수 있음 - 개별 단위가 뚜렷한 卍자 십자형인지로 구분) / 단순 격자문·창살문
(격자는 卍자 특유의 꺾임이 없는 순수 직교 반복)
판정 보류: 반복 패턴이 너무 작아 개별 단위가 卍자인지 단순 격자인지 확인 불가
""".strip(),
    "파형문": """
[파형문] (기하문, 巴形文·巴紋, 갈래 수에 따라 二巴紋·三巴紋 등으로 세분)
정의: 쉼표(물방울) 모양의 단위가 중심축을 기준으로 회전 대칭을 이루며 반복되는
기하문. 태극문과 형태적으로 가깝다.
필수에 가까운 특징: 쉼표·물방울 모양의 단위가 중심을 축으로 회전 대칭 배치됨 /
갈래 수(보통 2~4개)가 일정하게 반복됨
보조 특징: 태극문·팔괘문과 함께 등장 / 방패형·문장(紋章)형 장식의 중심 문양으로
사용
혼동 대상: 태극문(정확히 2개 갈래가 S자로 맞물리고 두 갈래가 서로 다른 색이나
음양으로 뚜렷이 대비되면 태극문 - 갈래 수가 3개 이상이거나 대비가 없으면
파형문) / 소용돌이 구름문(구름문은 갈래 수가 불규칙하고 자유로운 흐름형인
반면 파형문은 갈래 수가 고정되고 대칭적 회전 배치가 뚜렷함)
판정 보류: 회전 갈래 수를 정확히 셀 수 없을 만큼 마모되거나 가려짐
""".strip(),
    "팔괘문": """
[팔괘문] (기하문, 八卦紋)
정의: 주역(周易)의 8괘(건·태·리·진·손·감·간·곤)를 각각 3개의 효(가로 막대선)로
표현한 기하문. 방위·자연·가족 등을 상징.
필수에 가까운 특징: 3개의 가로 막대선(효)이 한 단위(괘)를 이루고, 이런 단위가
여러 개(최대 8개) 원형·방사형으로 배치됨 / 각 막대는 끊기지 않은 선(양효) 또는
중간이 끊긴 두 토막 선(음효)임
보조 특징: 중앙에 태극문(음양 소용돌이)과 함께 등장하는 경우가 많음 / 방위
(동서남북)를 상징하도록 원형으로 배치
혼동 대상: 태극문(중앙에 음양 소용돌이만 있고 막대선 괘가 둘레에 없으면
태극문 단독) / 단순 줄무늬·격자문(평행선 반복이라도 3줄씩 묶여 하나의 괘를
이루는 구조가 없으면 팔괘문이 아님)
판정 보류: 막대선이 몇 개씩 묶여 괘를 이루는지 마모·각도 때문에 셀 수 없음
""".strip(),
    "태극문": """
[태극문] (기하문, 太極文)
정의: 음양의 조화를 상징하는 두 갈래(또는 그 이상)가 서로 맞물려 도는 원형
문양. 태극기의 이파문(二巴紋)과 같은 계열.
필수에 가까운 특징: 원 안에서 두 개(또는 그 이상)의 소용돌이형 갈래가 서로
맞물려 회전 대칭을 이룸 / 두 갈래가 서로 다른 색이나 음각/양각으로 뚜렷이
대비됨(대비가 안 보이면 판정보류)
보조 특징: 팔괘문과 함께 둘레에 배치되는 경우가 많음 / 노리개, 인장, 도자기
저부 중앙 등 문양의 중심에 단독 배치되는 경우도 있음
혼동 대상: 파형문(회전 갈래가 3개 이상이면 삼파문 등 파형문으로 봐야 함 -
정확히 2개 갈래의 음양 대비면 태극문) / 소용돌이 구름문
판정 보류: 갈래 수나 음양 대비가 마모로 불명확
""".strip(),
}

# 별칭 - 위 PATTERN_CRITERIA에 별도 항목을 만들지 않고 다른 항목을 그대로
# 참조하도록 안내 문구만 붙인다 (내용 중복/불일치 방지).
PATTERN_CRITERIA["연꽃문"] = (
    "[연꽃문] 연화문과 같은 문양을 가리키는 표현입니다. 위 연화문 기준을 "
    "그대로 적용하세요."
)
PATTERN_CRITERIA["연판문"] = (
    "[연판문] 연화문 기준 설명에 포함된 표현입니다(연꽃잎 하나하나를 반복 "
    "배치해 테두리처럼 두른 형태 - 굽/구연부를 두르는 띠에 자주 사용). 위 "
    "연화문 기준을 그대로 적용하세요."
)
PATTERN_CRITERIA["덩굴문"] = (
    "[덩굴문] 독립된 별도 문양이 아니라 당초문의 하위개념이며, 대체로 "
    "인동문을 가리키는 표현입니다. 위 인동문 기준을 적용하되, 부채꼴 잎이 "
    "뚜렷하지 않으면 당초문(하위유형 불확실)으로 낮춰 답하세요."
)


class PatternLocationRaw(BaseModel):
    """VLM에게 실제로 요청하는 스키마. ensemble 후처리 필드는 포함하지 않는다."""

    pattern_family: Literal["동물문", "식물문", "자연문", "기하문", "기타"] = Field(
        description=(
            "문양 대분류. PATTERN_FAMILY_MAP 참고. 구름처럼 자연현상을 소재로 하면 자연문, "
            "뇌문/만자문/파형문/팔괘문/태극문처럼 반복되는 선·도형 자체가 상징인 경우 기하문."
        )
    )
    pattern_name: str = Field(
        description="가장 가능성 높은 문양명(primary candidate). 참고 목록에 없으면 가장 근접한 후보명을 적는다."
    )
    alternative_candidate: str | None = Field(
        default=None,
        description="두 번째로 가능성 있는 혼동 후보 문양명. 혼동 여지가 전혀 없으면 null.",
    )
    visible_evidence: list[str] = Field(
        default_factory=list,
        description="PATTERN_CRITERIA의 필수/보조 특징 중 사진에서 실제로 확인한 항목들을 간단히 적는다.",
    )
    missing_evidence: list[str] = Field(
        default_factory=list,
        description="필수에 가까운 특징 중 확인하지 못했거나 가려져서 안 보이는 항목들을 적는다.",
    )
    decision: Literal["확정", "추정", "판정보류"] = Field(
        description=(
            "확정=필수 특징을 대부분 직접 확인함, "
            "추정=필수 특징 일부만 확인했거나 보조 특징 위주로 판단함, "
            "판정보류=필수 특징을 거의 확인하지 못해 명칭을 단정할 수 없음"
        )
    )
    location_description: str = Field(description="사진에서 문양이 있는 위치 설명")
    pattern_scope: Literal[
        "small_instance",
        "large_continuous",
        "border_band",
    ] = Field(
        default="small_instance",
        description=(
            "small_instance=서로 분리된 작은 꽃/구름, "
            "large_continuous=용/봉황처럼 크게 이어진 문양, "
            "border_band=구연부/굽 둘레의 연속 띠 문양"
        ),
    )
    x_percent: float = Field(ge=0, le=100)
    y_percent: float = Field(ge=0, le=100)
    bbox_x1_percent: float | None = Field(default=None, ge=0, le=100)
    bbox_y1_percent: float | None = Field(default=None, ge=0, le=100)
    bbox_x2_percent: float | None = Field(default=None, ge=0, le=100)
    bbox_y2_percent: float | None = Field(default=None, ge=0, le=100)
    confidence: str = Field(description="VLM 자체 판단 확신도: 높음/중간/낮음")


class PotteryPatternAnalysisRaw(BaseModel):
    patterns: list[PatternLocationRaw]
    overall_description: str
    uncertainty: str


class PatternLocation(PatternLocationRaw):
    """단일/ensemble 결과에서 공통으로 쓰는 확장 모델.

    아래 필드는 VLM에게 요청하지 않고 코드에서 채운다.
    """

    agreement_count: int | None = None
    agreement_total: int | None = None
    location_agreement: float | None = None
    name_agreement: float | None = None
    name_votes: dict[str, int] | None = None
    display_name: str | None = None
    # 합의된(또는 단일 호출) 문양명이 참고 문양 목록에 있는지 여부.
    # False라고 해서 틀렸다는 뜻은 아니지만, 조사자가 더 주의 깊게 봐야 할
    # 신호로 사용한다.
    name_in_reference_list: bool | None = None
    # decision(확정/추정/판정보류)에 대한 호출별 투표 분포. 단일 호출
    # 결과에서는 채워지지 않고(None), ensemble 합의 단계에서만 채운다.
    decision_votes: dict[str, int] | None = None


class PotteryPatternAnalysis(BaseModel):
    patterns: list[PatternLocation]
    overall_description: str
    uncertainty: str


class PotteryPatternEnsembleResult(PotteryPatternAnalysis):
    """ensemble 함수 전용 반환 타입. 몇 번 호출이 성공했는지, 실제로
    적용된 합의 기준이 무엇인지까지 함께 담아 하위 코드(pottery_analyzer.py 등)가
    하드코딩된 임계값 대신 이 값을 참조할 수 있게 한다."""

    calls_requested: int = 0
    calls_succeeded: int = 0
    min_agreement_used: int = DEFAULT_MIN_AGREEMENT


_PATTERN_CRITERIA_TEXT = "\n\n".join(
    PATTERN_CRITERIA[name] for name in KNOWN_PATTERN_HINTS if name in PATTERN_CRITERIA
)

PROMPT_TEMPLATE = f"""이 이미지는 한국 전통 도자기 사진입니다.
표면에 실제로 보이는 전통 문양을 식별하고 위치를 좌표로 반환하세요.

참고 가능한 문양 이름:
{', '.join(KNOWN_PATTERN_HINTS)}

아래는 문양별 판별 기준입니다. 반드시 이 기준에 있는 "필수에 가까운 특징"을
사진에서 직접 찾아본 다음에 이름을 고르세요. 이름만 그럴듯하게 추측하지 말고,
근거가 부족하면 decision을 낮추고 missing_evidence에 확인 못한 항목을
적으세요.

{_PATTERN_CRITERIA_TEXT}

문양 범위 분류:
- small_instance: 서로 분리되어 보이는 작은 꽃, 작은 구름, 작은 장식 한 개
- large_continuous: 용, 봉황, 학처럼 몸체의 넓은 구간에 하나로 이어진 큰 문양
- border_band: 구연부, 어깨, 굽 둘레를 따라 이어진 띠 장식

매우 중요:
1. 하나의 연속된 대형 문양을 머리, 몸통, 꼬리처럼 부분별로 쪼개지 마세요.
   예를 들어 한 마리 용은 반드시 용문 한 항목으로만 반환하세요.
2. 반복되는 작은 문양은 서로 명확히 분리되어 보일 때만 개별 항목으로 반환하세요.
3. 확실하지 않은 개수를 억지로 늘리지 마세요. 최대 12개까지만 반환하세요.
4. 장식이 아닌 균열, 얼룩, 그림자, 반사광은 문양으로 반환하지 마세요.
5. 동일 위치를 서로 다른 문양 이름으로 중복 반환하지 마세요.
6. pattern_name에는 "{', '.join(FORBIDDEN_NAME_EXAMPLES)}"처럼 위치나
   부위를 가리키는 일반 명사를 절대 쓰지 마세요. border_band(띠 장식)라도
   그 띠 안에 실제로 그려진 문양이 무엇인지(예: 당초문, 뇌문, 연판문,
   여의두문 등) 구체적인 전통 문양명으로 답해야 합니다. 문양명을 정확히
   모르겠으면 가장 가능성 높은 후보 문양명을 적고 confidence를 "낮음"으로,
   decision을 "판정보류"로 낮추세요 - "위치 이름"으로 대체하는 것은 절대
   허용되지 않습니다.
7. 위 기준의 "혼동 대상"에 해당하는 다른 문양이 있다면 alternative_candidate에
   적으세요. 혼동 여지가 없다고 판단되면 null로 두세요 (문자열 "null"이 아니라
   실제 null 값이어야 합니다).
8. border_band(띠 장식) 항목 하나는 반드시 유물의 한 군데(예: 구연부
   아래만, 또는 굽 위만)만 가리켜야 합니다. 같은 이름의 띠 장식이 위
   (구연부 아래)와 아래(굽 위) 양쪽에 모두 있으면, 절대 한 항목의
   location_description에 "입구 아래와 굽 위 모두"처럼 두 곳을 함께
   적지 마세요 - 반드시 서로 다른 두 항목으로 나눠서, 각 항목의
   x_percent/y_percent/bbox가 그 항목이 실제로 설명하는 그 위치 하나만
   가리키게 하세요. location_description에는 그 항목이 있는 위치
   하나만 구체적으로 쓰세요(예: "구연부 바로 아래를 두르는 띠" 또는
   "굽 위쪽을 두르는 띠" - 두 위치를 한 문장에 같이 쓰지 마세요).
9. small_instance는 정말 작은 개별 문양 하나만 뜻합니다. bbox의 가로 또는 세로가
   사진의 30%를 넘거나 bbox 면적이 사진의 8%를 넘는다면 small_instance로 반환하지
   말고, 여러 문양을 묶은 설명도 만들지 마세요.
10. border_band는 유물 세로 길이의 일부만 차지하는 좁은 리본(가로띠) 형태입니다.
    bbox_y2_percent - bbox_y1_percent(세로 폭)가 사진 세로 길이의 20%를 넘으면
    안 됩니다. 구연부에서 굽까지 훑어보면서, 문양의 종류/밀도가 바뀌거나
    뚜렷한 경계선(돋을선, 음각선, 유약 색 경계)이 보일 때마다 그 사이사이를
    서로 다른 띠로 구분하세요. 예를 들어 "어깨 아래 첫 번째 띠"와 "몸체
    중간의 두 번째 띠"가 시각적으로 구분된다면, 절대 하나의 location_description에
    "어깨 아래 첫 번째 띠 장식과 몸체 중간 두 띠 장식"처럼 묶어서 쓰지 말고
    반드시 서로 다른 두 개 이상의 항목으로 나눠 각각의 bbox가 그 띠 하나만
    감싸게 하세요. 사진에 보이는 띠가 3개면 3개 항목을, 2개면 2개 항목을
    빠짐없이 반환하세요 - 일부만 골라서 반환하지 마세요.
11. decision은 다음 기준으로 정하세요.
   - 확정: 해당 문양의 필수에 가까운 특징을 대부분 직접 확인함
   - 추정: 필수 특징 일부만 확인했거나, 보조 특징 위주로 판단함
   - 판정보류: 필수 특징을 거의 확인하지 못해 이름을 단정할 수 없음
   (decision과 confidence는 다른 축입니다. confidence는 답변에 대한
   전반적 확신도, decision은 판별 기준을 얼마나 충족했는지입니다.)

각 항목에 다음을 포함하세요.
- pattern_family: 동물문/식물문/자연문/기타
- pattern_name: 가장 가능성 높은 후보명
- alternative_candidate: 혼동 가능한 다음 후보명 (없으면 null)
- visible_evidence: 실제로 확인한 근거 특징 목록
- missing_evidence: 확인하지 못한 필수 특징 목록
- decision: 확정/추정/판정보류
- location_description
- pattern_scope
- x_percent, y_percent: 문양 중심점
- bbox_x1_percent, bbox_y1_percent, bbox_x2_percent, bbox_y2_percent:
  large_continuous 또는 border_band일 때 문양 전체를 포함하는 대략적 박스.
  small_instance도 가능하면 박스를 주세요.
- confidence: 높음/중간/낮음

좌표 기준:
- 왼쪽 0, 오른쪽 100
- 위 0, 아래 100
- 박스는 x1 < x2, y1 < y2가 되게 하세요.
- 실제 문양 중심과 경계에 최대한 가깝게 추정하세요.

문양이 없으면 patterns는 빈 리스트로 반환하세요.
"""


def encode_image(image_path: str) -> str:
    with open(image_path, "rb") as file:
        return base64.b64encode(file.read()).decode("utf-8")


def _normalize_for_match(name: str) -> str:
    return name.strip().replace(" ", "")


def _in_reference_list(name: str) -> bool:
    """합의된(또는 단일 호출) 문양명이 참고 목록과 얼마나 겹치는지 느슨하게 확인한다.

    괄호 안 별칭(예: "학문(두루미)")까지 정확히 일치할 필요는 없으므로,
    괄호 앞부분만 뗀 핵심 명칭 기준으로 포함 관계도 함께 본다.
    """
    normalized = _normalize_for_match(name)
    if not normalized:
        return False
    for hint in KNOWN_PATTERN_HINTS:
        hint_core = _normalize_for_match(hint.split("(")[0])
        hint_full = _normalize_for_match(hint)
        if normalized in (hint_core, hint_full):
            return True
        if hint_core and (hint_core in normalized or normalized in hint_core):
            return True
    return False


# alternative_candidate가 실제 None이 아니라 "null"/"None"/""/"없음" 같은
# 문자열로 들어오는 사례가 실사용 테스트에서 확인됐다(모델이 JSON null 대신
# 그 단어를 문자열로 채운 경우). JSON에는 진짜 null이 나가야 하므로 정규화한다.
_NULL_LIKE_STRINGS = {"null", "none", "", "없음", "해당없음", "n/a", "na"}


def _normalize_alternative_candidate(value: str | None) -> str | None:
    if value is None:
        return None
    if value.strip().lower() in _NULL_LIKE_STRINGS:
        return None
    return value


_TOP_WORDS = ("입구", "구연", "목", "어깨", "상단")
_BOTTOM_WORDS = ("굽", "하단", "저부", "바닥")


def _sanitize_raw_patterns(
    patterns: list[PatternLocationRaw],
) -> list[PatternLocationRaw]:
    """VLM이 한 항목에 상단·하단 띠를 합치거나 작은 문양에 거대 bbox를 주는
    오류, alternative_candidate에 문자열 "null" 등을 넣는 오류를 보수적으로
    정리한다. 프롬프트 지시만으로는 100% 막을 수 없어 후처리로 이중 방어한다."""
    cleaned: list[PatternLocationRaw] = []
    for item in patterns:
        description = item.location_description or ""

        if item.pattern_scope == "border_band":
            has_top = any(word in description for word in _TOP_WORDS)
            has_bottom = any(word in description for word in _BOTTOM_WORDS)
            if has_top and has_bottom:
                common = item.model_dump()
                common["alternative_candidate"] = _normalize_alternative_candidate(
                    common.get("alternative_candidate")
                )
                top = dict(common)
                top.update(
                    {
                        "location_description": "구연부·목 주변의 상단 연속 띠 문양",
                        "x_percent": 50.0,
                        "y_percent": 22.0,
                        "bbox_x1_percent": 18.0,
                        "bbox_y1_percent": 12.0,
                        "bbox_x2_percent": 82.0,
                        "bbox_y2_percent": 34.0,
                        "confidence": "낮음" if item.confidence == "낮음" else "중간",
                    }
                )
                bottom = dict(common)
                bottom.update(
                    {
                        "location_description": "몸체 하단·굽 주변의 하단 연속 띠 문양",
                        "x_percent": 50.0,
                        "y_percent": 82.0,
                        "bbox_x1_percent": 18.0,
                        "bbox_y1_percent": 68.0,
                        "bbox_x2_percent": 82.0,
                        "bbox_y2_percent": 96.0,
                        "confidence": "낮음" if item.confidence == "낮음" else "중간",
                    }
                )
                cleaned.extend([PatternLocationRaw(**top), PatternLocationRaw(**bottom)])
                continue

            # 위/아래 키워드로는 안 걸리지만, bbox 세로 폭이 비정상적으로 큰 경우를
            # 잡아낸다. 실사용 테스트에서 "어깨 아래 첫 번째 띠 장식과 몸체 중간 두
            # 띠 장식"처럼 서로 다른 두 개 이상의 띠를 하나의 항목(y1=37%, y2=74%,
            # 즉 세로 36.7%를 차지)으로 합쳐 반환한 사례가 확인됐다. 진짜 "띠" 하나는
            # 유물 세로 길이의 일부만 차지하는 좁은 리본이므로, bbox 세로 폭이
            # BORDER_BAND_MAX_HEIGHT_PERCENT를 넘으면 절반으로 나눠 두 항목으로
            # 반환한다. 개별적으로 검증된 근거가 아니라 기계적으로 반씩 나눈
            # 것이므로 decision은 "추정"보다 높일 수 없게 낮춘다(이미 판정보류면 유지).
            band_values = (
                item.bbox_x1_percent,
                item.bbox_y1_percent,
                item.bbox_x2_percent,
                item.bbox_y2_percent,
            )
            if all(value is not None for value in band_values):
                band_height = float(item.bbox_y2_percent) - float(item.bbox_y1_percent)
                if band_height > BORDER_BAND_MAX_HEIGHT_PERCENT:
                    common = item.model_dump()
                    common["alternative_candidate"] = _normalize_alternative_candidate(
                        common.get("alternative_candidate")
                    )
                    mid_y = (float(item.bbox_y1_percent) + float(item.bbox_y2_percent)) / 2.0
                    downgraded_decision = (
                        "판정보류" if item.decision == "판정보류" else "추정"
                    )
                    split_note = (
                        "(하나의 항목이 서로 다른 띠 여러 개를 합쳐 반환한 것으로 "
                        "보여 절반으로 분리함 - 개별 검증되지 않은 근사치)"
                    )
                    upper = dict(common)
                    upper.update(
                        {
                            "location_description": f"{item.location_description} - 상단 절반 {split_note}",
                            "y_percent": (float(item.bbox_y1_percent) + mid_y) / 2.0,
                            "bbox_y1_percent": item.bbox_y1_percent,
                            "bbox_y2_percent": mid_y,
                            "decision": downgraded_decision,
                            "confidence": "낮음",
                        }
                    )
                    lower = dict(common)
                    lower.update(
                        {
                            "location_description": f"{item.location_description} - 하단 절반 {split_note}",
                            "y_percent": (mid_y + float(item.bbox_y2_percent)) / 2.0,
                            "bbox_y1_percent": mid_y,
                            "bbox_y2_percent": item.bbox_y2_percent,
                            "decision": downgraded_decision,
                            "confidence": "낮음",
                        }
                    )
                    cleaned.extend([PatternLocationRaw(**upper), PatternLocationRaw(**lower)])
                    continue

        if item.pattern_scope == "small_instance":
            values = (
                item.bbox_x1_percent,
                item.bbox_y1_percent,
                item.bbox_x2_percent,
                item.bbox_y2_percent,
            )
            if all(value is not None for value in values):
                width = float(item.bbox_x2_percent) - float(item.bbox_x1_percent)
                height = float(item.bbox_y2_percent) - float(item.bbox_y1_percent)
                if width > 30.0 or height > 30.0 or width * height > 800.0:
                    data = item.model_dump()
                    data.update(
                        {
                            "bbox_x1_percent": None,
                            "bbox_y1_percent": None,
                            "bbox_x2_percent": None,
                            "bbox_y2_percent": None,
                            "decision": "판정보류",
                            "confidence": "낮음",
                            "missing_evidence": sorted(
                                set(item.missing_evidence)
                                | {"개별 문양 하나의 경계를 특정하지 못함"}
                            ),
                        }
                    )
                    data["alternative_candidate"] = _normalize_alternative_candidate(
                        data.get("alternative_candidate")
                    )
                    item = PatternLocationRaw(**data)

        if item.alternative_candidate is not None:
            normalized_alt = _normalize_alternative_candidate(item.alternative_candidate)
            if normalized_alt != item.alternative_candidate:
                data = item.model_dump()
                data["alternative_candidate"] = normalized_alt
                item = PatternLocationRaw(**data)

        cleaned.append(item)
    return cleaned


def analyze_pottery_patterns(image_path: str) -> PotteryPatternAnalysis:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")

    client = OpenAI()
    response = client.beta.chat.completions.parse(
        model=MODEL_NAME,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT_TEMPLATE},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{encode_image(image_path)}"
                        },
                    },
                ],
            }
        ],
        response_format=PotteryPatternAnalysisRaw,
    )

    message = response.choices[0].message
    raw = message.parsed
    if raw is None:
        refusal = getattr(message, "refusal", None)
        raise RuntimeError(
            "VLM 응답 파싱 실패" + (f": {refusal}" if refusal else "")
        )

    sanitized_patterns = _sanitize_raw_patterns(raw.patterns)
    enriched_patterns = [
        PatternLocation(
            **raw_pattern.model_dump(),
            name_in_reference_list=_in_reference_list(raw_pattern.pattern_name),
        )
        for raw_pattern in sanitized_patterns
    ]
    return PotteryPatternAnalysis(
        patterns=enriched_patterns,
        overall_description=raw.overall_description,
        uncertainty=raw.uncertainty,
    )


def _cluster_threshold(scope: str) -> float:
    if scope == "large_continuous":
        return LARGE_CLUSTER_DISTANCE
    if scope == "border_band":
        return BORDER_CLUSTER_DISTANCE
    return SMALL_CLUSTER_DISTANCE


def _scope_compatible(a: str, b: str) -> bool:
    # 대형 문양과 띠 문양을 작은 반복 문양과 합치지 않는다.
    if a == b:
        return True
    return {a, b} <= {"large_continuous", "border_band"}


def _valid_bbox(item: PatternLocation) -> bool:
    values = (
        item.bbox_x1_percent,
        item.bbox_y1_percent,
        item.bbox_x2_percent,
        item.bbox_y2_percent,
    )
    if any(value is None for value in values):
        return False
    return bool(
        item.bbox_x1_percent < item.bbox_x2_percent
        and item.bbox_y1_percent < item.bbox_y2_percent
    )


def _average_optional(values: list[float | None]) -> float | None:
    valid = [float(v) for v in values if v is not None]
    return round(sum(valid) / len(valid), 1) if valid else None


def _percent_distance(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    image_width: int,
    image_height: int,
) -> float:
    """x_percent/y_percent 사이 거리를 이미지 가로세로 비율을 반영해 계산한다.

    기존에는 정사각형 이미지를 가정하고 (x_percent, y_percent)를 그대로
    유클리드 거리로 썼는데, 가로로 긴 사진에서는 x축 1%가 실제로는 더 넓은
    물리적 거리를 뜻하므로 클러스터링이 왜곡될 수 있었다. 픽셀 거리로 환산한
    뒤 다시 0~100 스케일로 정규화해 기존 임계값(SMALL/LARGE/BORDER_CLUSTER_DISTANCE)을
    그대로 재사용할 수 있게 했다. 정사각형 이미지에서는 기존 결과와 동일하다.
    """
    scale = max(image_width, image_height) / 100.0
    if scale <= 0:
        scale = 1.0
    pixel_x1, pixel_y1 = x1 / 100 * image_width, y1 / 100 * image_height
    pixel_x2, pixel_y2 = x2 / 100 * image_width, y2 / 100 * image_height
    pixel_distance = ((pixel_x1 - pixel_x2) ** 2 + (pixel_y1 - pixel_y2) ** 2) ** 0.5
    return pixel_distance / scale


def _build_consensus_summary(
    patterns: list[PatternLocation],
    min_agreement: int,
) -> str:
    confirmed = [
        pattern
        for pattern in patterns
        if (pattern.agreement_count or 0) >= min_agreement
    ]
    target = confirmed if confirmed else patterns
    if not target:
        return "사진에서 합의된 문양 후보를 식별하지 못했습니다."

    counts = Counter((pattern.display_name or pattern.pattern_name) for pattern in target)
    parts = [f"{name} {count}개" for name, count in counts.most_common()]
    qualifier = "합의된 주요 문양 후보" if confirmed else "낮은 합의를 포함한 문양 후보"
    return f"{qualifier}로 " + ", ".join(parts) + "가 관찰됩니다."


def _pattern_name_signature(text: str) -> frozenset[str]:
    """텍스트 안에 KNOWN_PATTERN_HINTS의 어떤 문양명들이 함께 언급됐는지를
    뽑아 "의미적 서명"으로 쓴다. 문장 전체를 비교하는 것보다, 같은 문양
    조합을 두고 벌어진 혼동인지를 훨씬 안정적으로 잡아낸다."""
    return frozenset(name for name in KNOWN_PATTERN_HINTS if name in text)


def _dedupe_similar_uncertainty_notes(notes: list[str]) -> list[str]:
    """호출마다 다른 문장으로 표현된, 사실상 같은 우려사항을 하나로 합친다.

    예: 3번의 VLM 호출이 모두 "인동문과 당초문 구분이 어렵다"는 취지를 각기
    다른 문장으로 냈다면, 그대로 이어붙일 경우 보고서에 거의 같은 말이 3번
    반복돼 장황해진다. 문장 전체의 문자 유사도로는 표현 차이 때문에 유사도가
    낮게 나와 걸러지지 않으므로, 대신 언급된 문양명 조합(_pattern_name_signature)이
    같은 문장들을 한 그룹으로 묶어 그중 가장 정보가 많은(긴) 문장만 대표로
    남긴다. 문양명이 전혀 언급되지 않은 문장은 그룹화하지 않고 그대로 둔다
    (서로 다른 내용을 잘못 하나로 합치는 것을 방지).
    """
    groups: dict[frozenset, list[str]] = defaultdict(list)
    order: list[frozenset] = []
    ungrouped: list[str] = []

    for raw_note in notes:
        note = raw_note.strip()
        if not note:
            continue
        signature = _pattern_name_signature(note)
        if not signature:
            ungrouped.append(note)
            continue
        if signature not in groups:
            order.append(signature)
        groups[signature].append(note)

    deduped: list[str] = []
    for signature in order:
        members = groups[signature]
        representative = max(members, key=len)
        if len(members) > 1:
            deduped.append(f"{representative} (유사 소견 {len(members)}건 통합)")
        else:
            deduped.append(representative)

    return deduped + ungrouped


def _run_calls_in_parallel(
    image_path: str,
    n_calls: int,
) -> tuple[list[PotteryPatternAnalysis], list[str]]:
    """서로 독립적인 VLM 호출을 병렬로 실행한다.

    개별 호출 실패는 나머지 호출 결과로 계속 진행할 수 있도록 예외를
    모아서 반환한다 (기존 순차 버전과 동일한 정책).
    """
    results: list[PotteryPatternAnalysis | None] = [None] * n_calls
    errors: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(n_calls, MAX_PARALLEL_CALLS)
    ) as executor:
        future_to_index = {
            executor.submit(analyze_pottery_patterns, image_path): index
            for index in range(n_calls)
        }
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            try:
                results[index] = future.result()
            except Exception as error:  # API 개별 실패는 나머지 호출로 계속 진행
                errors.append(f"호출 {index + 1}: {error}")
                print(f"[호출 {index + 1}/{n_calls}] 실패: {error}")

    successful_results = [result for result in results if result is not None]
    return successful_results, errors


def analyze_pottery_patterns_ensemble(
    image_path: str,
    n_calls: int = DEFAULT_N_CALLS,
    min_agreement: int = DEFAULT_MIN_AGREEMENT,
) -> PotteryPatternEnsembleResult:
    call_results, errors = _run_calls_in_parallel(image_path, n_calls)

    if not call_results:
        raise RuntimeError("모든 VLM 호출이 실패했습니다. " + "; ".join(errors))

    n_success = len(call_results)
    # n_calls를 min_agreement보다 작게 설정하면(예: n_calls=1) 어떤 항목도
    # 합의 기준을 못 넘겨 기본 화면이 항상 비어버리므로, 실제 성공한 호출
    # 수를 넘지 않는 선으로 유효 합의 기준을 낮춘다.
    effective_min_agreement = max(1, min(min_agreement, n_success))

    try:
        image_width, image_height = Image.open(image_path).size
    except Exception:
        image_width, image_height = 100, 100

    all_items: list[tuple[int, PatternLocation]] = []
    for call_index, result in enumerate(call_results):
        all_items.extend((call_index, pattern) for pattern in result.patterns)

    # 호출 순서의 영향을 줄이기 위해 대형/띠 문양을 먼저 배치한다.
    scope_order = {"large_continuous": 0, "border_band": 1, "small_instance": 2}
    all_items.sort(key=lambda pair: scope_order.get(pair[1].pattern_scope, 3))

    clusters: list[list[tuple[int, PatternLocation]]] = []

    for call_index, item in all_items:
        best_cluster_index: int | None = None
        best_distance = float("inf")

        for cluster_index, cluster in enumerate(clusters):
            # 같은 호출의 항목 두 개가 한 클러스터에 들어가 이름 투표와 평균 좌표를
            # 왜곡하지 않도록 호출당 한 항목만 허용한다.
            if call_index in {existing_call for existing_call, _ in cluster}:
                continue

            representative_scope = Counter(
                pattern.pattern_scope for _, pattern in cluster
            ).most_common(1)[0][0]
            if not _scope_compatible(item.pattern_scope, representative_scope):
                continue

            center_x = sum(pattern.x_percent for _, pattern in cluster) / len(cluster)
            center_y = sum(pattern.y_percent for _, pattern in cluster) / len(cluster)
            distance = _percent_distance(
                item.x_percent, item.y_percent, center_x, center_y, image_width, image_height
            )
            threshold = max(
                _cluster_threshold(item.pattern_scope),
                _cluster_threshold(representative_scope),
            )

            if distance <= threshold and distance < best_distance:
                best_cluster_index = cluster_index
                best_distance = distance

        if best_cluster_index is None:
            clusters.append([(call_index, item)])
        else:
            clusters[best_cluster_index].append((call_index, item))

    consensus_patterns: list[PatternLocation] = []

    for cluster in clusters:
        items = [item for _, item in cluster]
        agreement_count = len({call_index for call_index, _ in cluster})
        name_counts = Counter(item.pattern_name for item in items)
        scope_counts = Counter(item.pattern_scope for item in items)
        consensus_name, top_name_count = name_counts.most_common(1)[0]
        consensus_scope = scope_counts.most_common(1)[0][0]

        avg_x = round(sum(item.x_percent for item in items) / len(items), 1)
        avg_y = round(sum(item.y_percent for item in items) / len(items), 1)

        bbox_items = [item for item in items if _valid_bbox(item)]
        bbox_x1 = _average_optional([item.bbox_x1_percent for item in bbox_items])
        bbox_y1 = _average_optional([item.bbox_y1_percent for item in bbox_items])
        bbox_x2 = _average_optional([item.bbox_x2_percent for item in bbox_items])
        bbox_y2 = _average_optional([item.bbox_y2_percent for item in bbox_items])

        if agreement_count >= effective_min_agreement:
            confidence = "높음" if agreement_count == n_success else "중간"
        else:
            confidence = "낮음"

        name_breakdown = ", ".join(
            f"{name} {count}회" for name, count in name_counts.most_common()
        )
        name_note = f"; 명칭 투표: {name_breakdown}" if len(name_counts) > 1 else ""
        name_agreement = round(top_name_count / len(items), 3)
        if name_agreement >= 0.67:
            display_name = consensus_name
        else:
            top_names = [name for name, _count in name_counts.most_common(2)]
            display_name = "/".join(top_names) + " 계열(명칭 불확실)"

        # 판별 기준(pattern_family/decision/근거)은 consensus_name과 일치하는
        # 호출들만 대상으로 집계한다. 서로 다른 이름을 낸 호출의 근거를
        # 섞으면 "왜 이 이름을 골랐는지"가 뒤죽박죽이 되기 때문이다.
        matching_items = [item for item in items if item.pattern_name == consensus_name]
        family_counts = Counter(item.pattern_family for item in matching_items)
        consensus_family = (
            family_counts.most_common(1)[0][0]
            if family_counts
            else PATTERN_FAMILY_MAP.get(consensus_name, "기타")
        )

        decision_counts = Counter(item.decision for item in matching_items)
        # 다수결이되, 동률이면 더 보수적인(판정보류 > 추정 > 확정) 쪽을 우선한다.
        # 여러 호출이 서로 다른 확신도로 같은 이름을 냈다면, 다수결로 정확도를
        # 과대평가하기보다 신중한 쪽을 택하는 게 조사 도구의 취지에 맞다.
        conservativeness = {"판정보류": 2, "추정": 1, "확정": 0}
        if decision_counts:
            max_count = max(decision_counts.values())
            tied = [d for d, c in decision_counts.items() if c == max_count]
            consensus_decision = max(tied, key=lambda d: conservativeness.get(d, 0))
        else:
            consensus_decision = "판정보류"

        alt_counts = Counter(
            item.alternative_candidate for item in matching_items if item.alternative_candidate
        )
        consensus_alternative = alt_counts.most_common(1)[0][0] if alt_counts else None

        visible_evidence = sorted(
            {evidence for item in matching_items for evidence in item.visible_evidence}
        )
        missing_evidence = sorted(
            {evidence for item in matching_items for evidence in item.missing_evidence}
        )

        consensus_patterns.append(
            PatternLocation(
                pattern_family=consensus_family,
                pattern_name=consensus_name,
                alternative_candidate=consensus_alternative,
                visible_evidence=visible_evidence,
                missing_evidence=missing_evidence,
                decision=consensus_decision,
                location_description=(
                    f"[{agreement_count}/{n_success}회 위치 일치] "
                    f"{items[0].location_description}{name_note}"
                ),
                pattern_scope=consensus_scope,
                x_percent=avg_x,
                y_percent=avg_y,
                bbox_x1_percent=bbox_x1,
                bbox_y1_percent=bbox_y1,
                bbox_x2_percent=bbox_x2,
                bbox_y2_percent=bbox_y2,
                confidence=confidence,
                agreement_count=agreement_count,
                agreement_total=n_success,
                location_agreement=round(agreement_count / n_success, 3),
                name_agreement=name_agreement,
                name_votes=dict(name_counts),
                display_name=display_name,
                name_in_reference_list=_in_reference_list(consensus_name),
                decision_votes=dict(decision_counts) if decision_counts else None,
            )
        )

    uncertainty_notes = [
        result.uncertainty
        for result in call_results
        if result.uncertainty and result.uncertainty != "없음"
    ]
    # 호출마다 다른 문장으로 표현된 사실상 동일한 우려사항을 하나로 합친다
    # (예: "인동문/당초문 구분 어려움"을 3번 호출이 각기 다르게 표현한 경우).
    uncertainty_notes = _dedupe_similar_uncertainty_notes(uncertainty_notes)
    uncertainty_notes.extend(errors)

    return PotteryPatternEnsembleResult(
        patterns=consensus_patterns,
        overall_description=_build_consensus_summary(consensus_patterns, effective_min_agreement),
        uncertainty="; ".join(uncertainty_notes) if uncertainty_notes else "없음",
        calls_requested=n_calls,
        calls_succeeded=n_success,
        min_agreement_used=effective_min_agreement,
    )


# ---------------------------------------------------------------------------
# 클로즈업 육안상태조사 (v9, v10에서 판정 언어 완화)
#
# 배경: 문양의 정밀한 픽셀 단위 위치/마스크를 맞추려는 시도가 이번 프로젝트
# 내내 반복적으로 새로운 버그를 만들어냈다(구연부 침범, 창 클리핑, 오버사이즈
# bbox 병합 등). 반면 VLM은 "어디 있는지 정확히 짚기"보다 "확대된 이미지를
# 자세히 들여다보고 상태를 설명하기"에 훨씬 강하다는 특성이 있다. 그래서
# 정밀 마스크 대신, 이미 확정된(default_keys) 문양마다 원본 사진에서 넉넉하게
# 크롭한 클로즈업 이미지를 VLM에 다시 한번 보여주고, 그 문양의 보존 상태
# 후보(마모/박락/변색/균열/오염 의심)를 평가하도록 요청한다.
#
# 설계상 선택:
# - 크롭 박스는 "정확한 경계"가 아니라 "이 근처를 넉넉히 포함"하면 되므로
#   기존의 타이트닝/배타영역 로직을 그대로 재사용하되 정밀도 요구는 낮췄다
#   (pottery_analyzer.py의 크롭 박스 계산 참고).
# - 비용/시간을 고려해 이 단계는 앙상블(반복 호출)하지 않고 문양당 단일
#   호출만 한다. 위치/명칭 신뢰도는 이미 1차 ensemble 단계에서 확보했으므로,
#   이 단계는 "상태 평가"라는 별도 축을 한 번만 더 확인하는 것으로 충분하다고
#   판단했다.
#
# v10 수정 (실사용 테스트 리뷰 반영): 처음 버전은 condition_status를
# "양호/경미한마모/부분훼손/심한훼손"처럼 확정적인 어투로 반환했는데, 실제
# 테스트에서 confidence는 "중간"으로 낮춰놓고도 상태 라벨은 "부분훼손"처럼
# 단정적으로 나오는 내적 모순이 있었다. 사진 한 장만으로는 마모(실제 손상)와
# 촬영 화질 저하(블러/압축/초점)를, 박락(안료 결손)과 원래 있던 얼룩·오염·
# 이미지 노이즈를, 변색과 유약색·조명 차이를 구분하기 어렵다는 지적이 맞았다.
# 문양 명칭 쪽은 이미 결정(확정/추정/판정보류)으로 조심스럽게 설계해놓고
# 상태 평가만 그 원칙을 벗어난 셈이라 통일시켰다. 이제:
# - condition_status는 "이상 의심/관찰" 계열 표현만 쓰고 "훼손 확정" 계열
#   표현(부분훼손/심한훼손 등)은 쓰지 않는다.
# - 문제 목록(issues)은 평평한 문자열 목록이 아니라, 항목별로 confidence와
#   "손상이 아닐 수도 있는 대안 설명(alternative_explanation)"을 함께
#   반환하는 구조로 바꿔, 사람이 검토할 때 무엇을 의심해야 하는지와 동시에
#   무엇 때문에 오판일 수 있는지를 같이 보게 했다.
# - human_review_required 필드를 추가해 "이 결과는 AI의 최종 판정이 아니라
#   사람이 검토해야 할 후보 목록"이라는 점을 스키마 차원에서 명시했다.
# ---------------------------------------------------------------------------


class ConditionIssueCandidate(BaseModel):
    issue_type: str = Field(
        description=(
            "관찰된 이상 후보를 구체적으로 서술. 예: '문양선 마모 의심', "
            "'국소 표면 결손 의심', '색상 변화', '균열 의심' 등. "
            "'마모'/'박락' 같은 단정적 결론어보다 '~의심'/'~후보'로 표현할 것."
        )
    )
    confidence: str = Field(description="이 개별 항목에 대한 확신도: 높음/중간/낮음")
    alternative_explanation: str | None = Field(
        default=None,
        description=(
            "이 관찰이 실제 손상이 아닐 수도 있는 대안 설명(사진 초점/해상도/"
            "압축, 조명·반사, 원래 유약색, 촬영 각도 등). 손상이 아닐 가능성이 "
            "낮다고 판단되면 null."
        ),
    )


class PatternConditionAssessment(BaseModel):
    condition_status: Literal[
        "특이사항 없음",
        "경미한 이상 의심",
        "이상 후보 관찰",
        "뚜렷한 이상 의심",
        "판정불가",
    ] = Field(
        description=(
            "클로즈업 이미지에서 관찰한 이상 징후의 대략적인 정도. 이 사진 한 장만으로는 "
            "실제 훼손 여부를 확정할 수 없으므로, 모든 단계는 '의심/관찰' 수준의 표현이며 "
            "'양호/훼손 확정'처럼 단정하지 않는다."
        )
    )
    issues: list[ConditionIssueCandidate] = Field(
            default_factory=list,
            description=(
                "관찰된 이상 후보 목록(항목별 confidence·대안설명 포함). 없으면 빈 리스트. "
                "주의: 사진 화질, 조명, 유약 반사, 촬영 각도만으로 충분히 설명되는 사소한 "
                "변화는 여기에 넣지 마세요. 대부분의 정상적인 사진에는 이런 약한 신호가 "
                "항상 있을 수 있는데, 그걸 전부 이슈로 등록하면 진짜 중요한 이슈를 구분할 "
                "수 없게 됩니다. 실제로 훼손을 의심할 만한 뚜렷한 근거가 있을 때만 등록하세요."
            ),
        )
    condition_description: str = Field(
        description="상태 후보에 대한 자연어 설명 1~2문장. 단정적 어투를 피하고 '~로 보임', '~의심' 등으로 서술."
    )
    human_review_required: bool = Field(
            description=(
                "이 클로즈업만으로 최종 판정하지 말고 사람(조사자)이 고해상도 원본이나 "
                "실물로 재확인해야 하는지 여부. 이상 후보 중 confidence가 '높음'인 것이 "
                "하나라도 있을 때만 true로 하세요. confidence가 '중간'이거나 '낮음'인 "
                "후보만 있는 경우는 false로 하세요 - '중간' 확신도는 조사자가 매번 "
                "재확인해야 할 만큼 심각한 신호가 아닙니다."
            )
        )
    confidence: str = Field(description="전체 평가에 대한 VLM 자체 확신도: 높음/중간/낮음")


CONDITION_PROMPT_TEMPLATE = """이 이미지는 한국 전통 도자기 표면의 한 문양 부분을 클로즈업(확대)한
사진입니다. 참고로 이 부분은 이전 분석에서 '{pattern_name}'({decision} 판정)으로
식별된 위치입니다.

이 클로즈업 이미지에서 문양의 보존 상태를 살펴보세요. 문양의 이름을 다시
판별하는 것이 아니라, 이상 징후 "후보"를 찾아 사람이 재확인하도록 돕는
것이 목적입니다. 사진 한 장만으로는 실제 훼손 여부를 확정할 수 없다는
점을 항상 염두에 두세요.

확인할 항목(모두 "의심/후보" 수준으로만 판단):
- 문양선 마모 의심: 문양의 선이나 색이 흐려지거나 얕아진 것처럼 보이는 부분
  (단, 사진 초점이 안 맞거나 해상도가 낮아도 비슷하게 보일 수 있음)
- 국소 표면 결손 의심(박락): 유약이나 안료가 벗겨져 떨어진 것처럼 보이는 부분.
  단순히 어두운 점이 아니라, 표면층이 떨어져 나간 것으로 보이는 뚜렷한
  경계·단차가 보일 때만 이 항목으로 판단하세요. 그냥 어두운 점이나 얼룩은
  오염/원래 반점/이미지 노이즈일 가능성이 더 큽니다.
- 색상 변화: 원래 색과 다르게 바래거나 얼룩진 것처럼 보이는 부분 (단, 유약
  자체의 색, 조명·반사 차이일 수도 있음)
- 균열 의심: 표면에 금이 가거나 갈라진 것처럼 보이는 부분
- 오염: 이물질, 얼룩 등 원래 문양이 아닌 것으로 보이는 부착물

각 관찰에 대해 반드시 "이게 실제 손상이 아닐 수도 있는 이유"를 함께
생각해보고 alternative_explanation에 적으세요(예: 사진 압축/블러, 반사광,
유약 자체의 색, 촬영 각도). 대안 설명이 딱히 없다고 판단되면 null로
두세요.

다음을 반환하세요:
- condition_status: 특이사항 없음 / 경미한 이상 의심 / 이상 후보 관찰 /
  뚜렷한 이상 의심 / 판정불가(사진 화질·각도 문제로 판단 자체가 어려움).
  절대 "양호"나 "훼손"처럼 단정적으로 결론짓지 마세요.
- issues: 관찰된 이상 후보 목록. 각 항목은 issue_type(구체적 서술,
  '~의심'/'~후보' 표현), confidence(높음/중간/낮음),
  alternative_explanation(손상이 아닐 수도 있는 이유, 없으면 null)로 구성.
  특이사항이 없으면 빈 리스트.
- condition_description: 상태 후보에 대한 자연어 설명 (1~2문장, 단정적
  어투 대신 '~로 보임'/'~의심됨' 등으로 서술)
- human_review_required: 이 클로즈업만으로 최종 판정하지 말고 사람이
  고해상도 원본이나 실물로 재확인해야 하면 true (이상 후보가 하나라도
  있으면 기본적으로 true)
- confidence: 전체 평가에 대한 확신도: 높음/중간/낮음

주의: 사진 자체의 화질 저하(흐림, 저해상도, 반사광, 압축)와 실제 유물
표면의 손상은 구분하기 어렵습니다. 구분이 어려우면 confidence를 낮추고
condition_status를 "판정불가"로 하세요.
"""


def encode_pil_image(image: Image.Image) -> str:
    """이미 메모리에 있는 PIL 이미지를 base64로 인코딩한다(디스크 저장 없이
    크롭 이미지를 바로 VLM에 보낼 때 사용)."""
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=92)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")

class EraEvidence(BaseModel):
    supporting_evidence: list[str] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "이미지 분류 모델이 예측한 시대와 실제로 사진에서 관찰되는, 그 시대 "
            "양식과 부합하는 구체적 근거. 완결된 문장이 아니라 짧은 구(句) 형태로 "
            "적을 것(예: '두껍게 시유된 백자 유약과 낮은 굽'). 항목당 20자 내외. "
            "근거 없이 시대명만 반복하지 말 것. 최대 3개."
        ),
    )
    conflicting_evidence: list[str] = Field(
        default_factory=list,
        max_length=2,
        description=(
            "반대로 이 시대라고 보기엔 어색하거나 다른 시대 특징에 더 가까워 "
            "보이는 부분. 마찬가지로 짧은 구(句) 형태로 적을 것(예: '중국계 "
            "도안에 가까운 화려한 띠무늬'). 항목당 20자 내외. 없으면 빈 리스트."
        ),
    )
    reasoning_confidence: Literal["높음", "중간", "낮음"] = Field(
        description=(
            "위에서 든 근거들이 전반적으로 이 시대 판단을 얼마나 뒷받침하는지에 "
            "대한 자체 평가. 분류 모델의 확신도가 아니라, 지금 제시한 근거 자체의 "
            "설득력을 평가할 것."
        )
    )


ERA_EVIDENCE_PROMPT_TEMPLATE = """이 도자기 사진은 별도의 이미지 분류 모델이 형태·양식을 기준으로
'{era}' 시대로 예측했습니다. 당신의 역할은 이 시대를 처음부터 다시 추정하는 게
아니라, 이미 나온 예측이 실제로 사진에서 보이는 근거와 맞는지 검토하는 것입니다.

'{era}' 시대 도자기의 전형적 특징(기형, 문양 양식, 유약, 굽 처리 방식 등)을
떠올리고, 그런 특징이 이 사진에서 실제로 관찰되는지 확인하세요.

- supporting_evidence: 이 사진에서 실제로 보이는, '{era}' 시대와 부합하는
  구체적인 특징을 적으세요. **완결된 문장이 아니라 짧은 구(句)로** 적으세요
  (예: "두껍게 시유된 백자 유약과 낮은 굽" - 이런 식으로 20자 내외). 풀어서
  긴 문장으로 설명하지 마세요. 최대 3개.
- conflicting_evidence: 반대로 '{era}'라고 보기엔 어색하거나, 다른 시대 특징에
  더 가까워 보이는 부분이 있다면 마찬가지로 짧은 구로 적으세요. 없으면 빈
  리스트로 두세요.
- reasoning_confidence: 위 근거들이 전반적으로 이 시대 판단을 얼마나 뒷받침하는지
  스스로 평가하세요.

모르는 걸 지어내지 마세요 - 뚜렷한 근거가 없으면 supporting_evidence를 비워두고
reasoning_confidence를 '낮음'으로 주세요."""


def explain_era_prediction(image_path: str, era: str) -> EraEvidence:
    """CNN이 내놓은 시대 예측에 대해, 실제로 사진에서 보이는 근거를 VLM에게
    검토시킨다.

    CNN을 대체하는 게 아니다 - CNN이 문양(55~64%)/색상(33%)보다 정확도가 훨씬
    높은 88%였기 때문에 시대 판정 자체는 그대로 CNN에 맡긴다. 이 함수는 그
    판단에 "왜 이 시대로 보이는지" 사람이 검증 가능한 근거를 붙이는 역할만
    한다 - 전문가가 보고서를 읽었을 때 "아 맞네" 하고 넘어가거나 "이 근거는
    이상한데?" 하고 되짚어볼 수 있게 하기 위함이다.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")

    client = OpenAI()
    image = Image.open(image_path)
    prompt = ERA_EVIDENCE_PROMPT_TEMPLATE.format(era=era)
    response = client.beta.chat.completions.parse(
        model=MODEL_NAME,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{encode_pil_image(image)}"
                        },
                    },
                ],
            }
        ],
        response_format=EraEvidence,
    )

    message = response.choices[0].message
    parsed = message.parsed
    if parsed is None:
        refusal = getattr(message, "refusal", None)
        raise RuntimeError(
            "시대 판단 근거 파싱 실패" + (f": {refusal}" if refusal else "")
        )
    return parsed
def assess_pattern_condition(
    cropped_image: Image.Image,
    pattern_name: str,
    decision: str,
) -> PatternConditionAssessment:
    """문양 하나의 클로즈업 크롭 이미지를 받아 보존 상태를 단일 호출로 평가한다.

    앙상블(반복 호출)하지 않는다 - 위치/명칭 신뢰도는 이미 1차 ensemble
    단계에서 확보했으므로, 상태 평가는 문양당 1회 호출로 비용/시간을 억제한다.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")

    client = OpenAI()
    prompt = CONDITION_PROMPT_TEMPLATE.format(pattern_name=pattern_name, decision=decision)
    response = client.beta.chat.completions.parse(
        model=MODEL_NAME,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{encode_pil_image(cropped_image)}"
                        },
                    },
                ],
            }
        ],
        response_format=PatternConditionAssessment,
    )

    message = response.choices[0].message
    parsed = message.parsed
    if parsed is None:
        refusal = getattr(message, "refusal", None)
        raise RuntimeError(
            "클로즈업 상태 평가 파싱 실패" + (f": {refusal}" if refusal else "")
        )
    return parsed


def assess_pattern_conditions_in_parallel(
    jobs: list[tuple[str, Image.Image, str, str]],
    max_workers: int = MAX_PARALLEL_CALLS,
) -> dict[str, PatternConditionAssessment | Exception]:
    """여러 문양의 클로즈업 상태 평가를 병렬로 실행한다.

    jobs: (key, cropped_image, pattern_name, decision) 튜플 목록. 개별 호출
    실패는 예외 객체로 결과에 담아 반환하고, 나머지 호출은 계속 진행한다
    (ensemble 병렬 호출과 동일한 정책).
    """
    results: dict[str, PatternConditionAssessment | Exception] = {}
    if not jobs:
        return results

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(len(jobs), max_workers)
    ) as executor:
        future_to_key = {
            executor.submit(assess_pattern_condition, image, name, decision): key
            for key, image, name, decision in jobs
        }
        for future in concurrent.futures.as_completed(future_to_key):
            key = future_to_key[future]
            try:
                results[key] = future.result()
            except Exception as error:
                results[key] = error
                print(f"[클로즈업 상태 평가 실패] {key}: {error}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--n-calls", type=int, default=DEFAULT_N_CALLS)
    parser.add_argument("--single-call", action="store_true")
    arguments = parser.parse_args()

    if arguments.single_call:
        analysis = analyze_pottery_patterns(arguments.image)
    else:
        analysis = analyze_pottery_patterns_ensemble(
            arguments.image,
            n_calls=arguments.n_calls,
        )

    print(json.dumps(analysis.model_dump(), ensure_ascii=False, indent=2))
