from dotenv import load_dotenv
load_dotenv()

import os
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-5.4-nano", temperature=0, api_key=os.environ["OPENAI_API_KEY"])