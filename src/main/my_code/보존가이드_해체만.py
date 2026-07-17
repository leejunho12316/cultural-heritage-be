from datetime import timedelta, datetime
from typing import Annotated, Literal, Optional, TypedDict
from pydantic import BaseModel, Field
import json, functools, os
from langgraph.types import interrupt
from langchain_openai import ChatOpenAI

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
import sqlite3

os.environ.get("OPENAI_API_KEY")
llm = ChatOpenAI(model="gpt-5.4-nano", temperature=0)

# 처리 단계의 표준 순서. 노드는 항상 이 순서로 연결되며 실제 실행 여부는 State["flow"] (사람이 선택한 flow) 에 포함됐는지로 결정된다.
MASTER_ORDER = [
    "survey",            # 처리 전 조사
    "disassembly",       # 해체
    "cleaning",          # 세척
    "reinforcement",     # 강화처리
    "joining",           # 접합
    "restoration",       # 복원
    "color_matching",    # 색맞춤
    "document",          # 처리 후 기록
]






