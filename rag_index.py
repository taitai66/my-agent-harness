# rag_index.py
import os
import sys
from tools.rag import THE_RAG
from config import WORK_DIR

def index_files(file_paths):
    for path in file_paths:
        full_path = os.path.join(WORK_DIR, path)
        if not os.path.exists(full_path):
            print(f"跳过：{path} 不存在")
            continue
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        
        num = THE_RAG.add_document(path, content)
        print(f"已索引 {path}，共 {num} 个块")

if __name__ == "__main__":
    # 把你关心的文档写在这里
    files_to_index = ["CHANGELOG.md", "note.txt", "main.py"]
    print("开始构建知识库...")
    index_files(files_to_index)
    print("知识库构建完成！")