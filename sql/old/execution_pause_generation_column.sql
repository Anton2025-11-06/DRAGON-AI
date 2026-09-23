-- 工作流执行表加「挂起代次」列（契约见 docs/workflow-execution-contract.md §3.1）
-- 用途：事件帧带代次，消费方据此丢弃过期帧（取代订阅端的三处特判）。
-- 语义：第几次开跑（首跑与每次唤醒各算一次）；挂起不推进，同一次运行的帧必须同代次。
-- 存量行不批量 UPDATE：代次只用于新旧帧判定，DEFAULT 1 与存量行为等价。
ALTER TABLE tb_workflow_execution
    ADD COLUMN pause_generation INT NOT NULL DEFAULT 1
        COMMENT '挂起代次：第几次开跑（含唤醒），事件过期判定用';

-- 已按早期版本执行过的库，只需刷一下列注释（可选，不影响行为）：
-- ALTER TABLE tb_workflow_execution
--     MODIFY COLUMN pause_generation INT NOT NULL DEFAULT 1
--         COMMENT '挂起代次：第几次开跑（含唤醒），事件过期判定用';
