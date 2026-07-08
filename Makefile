.PHONY: up down init clean

MILVUS_DIR = docker/milvus

# 启动 Milvus
up:
	docker-compose -f $(MILVUS_DIR)/docker-compose.yml up -d
	@echo "等待 Milvus 启动..."
	@sleep 10
	@echo "Milvus 已启动 (端口 19530)"

# 停止 Milvus
down:
	docker-compose -f $(MILVUS_DIR)/docker-compose.yml down

# 查看状态
status:
	docker-compose -f $(MILVUS_DIR)/docker-compose.yml ps

# 查看日志
logs:
	docker-compose -f $(MILVUS_DIR)/docker-compose.yml logs -f

# 完全清理（删除数据）
clean:
	docker-compose -f $(MILVUS_DIR)/docker-compose.yml down -v
	-rm -rf $(MILVUS_DIR)/volumes
	@echo "Milvus 数据已清理"

# 初始化知识库 Collection
init:
	cd bot/langgraph_workflow && python -c "from services.vector_store import VectorStore; VectorStore().create_collection()"
	@echo "知识库 Collection 已创建"

# 重启 Milvus
restart: down up