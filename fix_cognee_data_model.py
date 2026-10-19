import sys


def main():
    content = open("packages/cognee_duckdb/cognee/tests/test_delete_default_graph.py").read()
    content = content.replace(
        'KnowledgeGraph(name="test")',
        'KnowledgeGraph(name="test", description="desc", summary="summary")',
    )
    content = content.replace(
        'Node(id=1, name="node1")', 'Node(id=1, name="node1", label="label1")'
    )
    content = content.replace(
        'Node(id=2, name="node2")', 'Node(id=2, name="node2", label="label2")'
    )
    content = content.replace(
        'Node(id=3, name="node3")', 'Node(id=3, name="node3", label="label3")'
    )
    content = content.replace(
        'Edge(source_node_id=1, target_node_id=2, relationship_name="test_rel")',
        'Edge(source_node_id=1, target_node_id=2, relationship_name="test_rel", description="test_rel_desc")',
    )
    content = content.replace(
        'Edge(source_node_id=2, target_node_id=3, relationship_name="test_rel_2")',
        'Edge(source_node_id=2, target_node_id=3, relationship_name="test_rel_2", description="test_rel_2_desc")',
    )
    open("packages/cognee_duckdb/cognee/tests/test_delete_default_graph.py", "w").write(content)


main()
