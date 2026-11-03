# tools/rag.py
import json
import os
import sqlite3
import math
import re
import threading
from config import WORK_DIR

DB_PATH = os.path.join(WORK_DIR, ".rag.db")

def chunk_text(text, size=300, overlap=50):
    """把长文本切块"""
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start += size - overlap
    return chunks

def extract_words(text):
    """提取中文字符和英文单词，用于关键词匹配"""
    return re.findall(r'[\u4e00-\u9fa5]|[a-zA-Z]+', text.lower())

def get_vector(text):
    """极简向量化：提取词，做 256 维哈希映射（模拟 Dense Embedding）"""
    vec = [0.0] * 256
    words = extract_words(text)
    for w in words:
        idx = hash(w) % 256
        vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec

class RagStore:
    def __init__(self):
        # check_same_thread=False 允许跨线程使用连接
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.lock = threading.Lock()  # 新增一把锁，防止并发读写冲突
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                text TEXT,
                vector TEXT
            )
        """)
        self.conn.commit()

    def add_document(self, source, content):
        chunks = chunk_text(content)
        with self.lock:
            for c in chunks:
                vec = get_vector(c)
                self.conn.execute(
                    "INSERT INTO chunks (source, text, vector) VALUES (?, ?, ?)",
                    (source, c, json.dumps(vec))
                )
            self.conn.commit()
        return len(chunks)

    def search(self, query, top_k=3):
        """混合检索：向量相似度（语义） + 关键词命中率（精确匹配）"""
        query_vec = get_vector(query)
        query_words = set(extract_words(query))
        
        with self.lock:
            cursor = self.conn.execute("SELECT source, text, vector FROM chunks")
            rows = cursor.fetchall()  # 一次性取出来，释放游标，防止后续操作锁库
        
        results = []
        for row in rows:
            source, text, vec_str = row
            vec = json.loads(vec_str)
            
            # 1. Dense 得分（余弦相似度）
            vector_score = sum(q * v for q, v in zip(query_vec, vec))
            
            # 2. Sparse 得分（关键词召回率）
            text_words = set(extract_words(text))
            if len(query_words) > 0:
                keyword_score = len(query_words & text_words) / len(query_words)
            else:
                keyword_score = 0.0
                
            # 3. 加权融合（各占 50%）
            final_score = 0.5 * vector_score + 0.5 * keyword_score
            
            if final_score > 0.1:  # 过滤掉完全不相关的
                results.append((final_score, source, text))
        
        # 排序并返回 Top K
        results.sort(key=lambda x: x[0], reverse=True)
        return results[:top_k]

# 全局唯一的数据库实例
THE_RAG = RagStore()

class RagSearchTool:
    def definition(self):
        return {
            "name": "rag_search",
            "description": "在本地项目知识库（包含项目文档、笔记、历史代码等）中搜索信息。"
                           "当你被问及涉及项目特定配置、历史记录、专有代码名词或你不确定的事实时，"
                           "优先调用此工具。它结合了语义理解和关键词精确匹配。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词或用户的完整问题"}
                },
                "required": ["query"]
            }
        }

    def execute(self, args, ctx):
        try:
            in_ = json.loads(args) or {}
        except json.JSONDecodeError as e:
            return "错误: 参数不是合法 JSON: " + str(e)
        
        query = in_.get("query", "")
        if not query:
            return "错误: query 不能为空"

        results = THE_RAG.search(query, top_k=3)
        if not results:
            return "在知识库中没有找到相关的内容。"

        parts = []
        for score, source, text in results:
            parts.append(f"[来源: {source} | 匹配度: {score:.2f}]\n{text}")
        
        return "\n\n---\n\n".join(parts)