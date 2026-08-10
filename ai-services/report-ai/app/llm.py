from dotenv import load_dotenv
load_dotenv()

import os
from langchain_openai import ChatOpenAI

# 보고서 섹션 작성 노드에서 사용하는 텍스트 모델.
llm = ChatOpenAI(model="gpt-5.4-nano", temperature=0, api_key=os.environ["OPENAI_API_KEY"])

# 스캔 페이지 OCR 폴백(build_index.py)에서만 사용하는 vision 지원 모델.
vision_llm = ChatOpenAI(model="gpt-4o", temperature=0, api_key=os.environ["OPENAI_API_KEY"])
