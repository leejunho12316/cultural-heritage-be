from dotenv import load_dotenv
load_dotenv()

import os
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-5.4-nano", temperature=0, api_key=os.environ["OPENAI_API_KEY"])

# 이미지 입력이 필요한 노드(강화처리 습윤효과 테스트 등)에서 사용하는 vision 지원 모델
vision_llm = ChatOpenAI(model="gpt-4o", temperature=0, api_key=os.environ["OPENAI_API_KEY"])