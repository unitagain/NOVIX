/**
 * U5 · PR-3 交互组件的展示决策回归。
 *
 * 重点：interrupted 不得被判为 done（plan.md §4「incomplete 不伪装 completed」）。
 */
import { describe, expect, it } from 'vitest';
import { currentStepIndex, planOutcome } from './PlanTaskCard';
import { answeredCount } from './ClarificationPart';

const step = (id, status) => ({ id, status, description: `第 ${id} 步` });

describe('PlanTaskCard.planOutcome', () => {
  it('执行中为 running', () => {
    expect(planOutcome([step(1, 'pending')], { executing: true })).toBe('running');
  });

  it('全部完成为 done', () => {
    expect(planOutcome([step(1, 'done'), step(2, 'done')])).toBe('done');
  });

  it('任一步失败为 failed', () => {
    expect(planOutcome([step(1, 'done'), step(2, 'failed')])).toBe('failed');
  });

  it('未失败但有未完成步为 interrupted，不得判为完成', () => {
    expect(planOutcome([step(1, 'done'), step(2, 'pending')])).toBe('interrupted');
    expect(planOutcome([step(1, 'pending')])).toBe('interrupted');
  });

  it('无步骤为 pending', () => {
    expect(planOutcome([])).toBe('pending');
  });
});

describe('PlanTaskCard.currentStepIndex', () => {
  it('优先按事件里的 step_id 定位当前步（1-based）', () => {
    expect(currentStepIndex([step(1, 'done'), step(2, 'pending'), step(3, 'pending')], 2)).toBe(2);
  });

  it('step_id 缺失时回落为已完成步数', () => {
    expect(currentStepIndex([step(1, 'done'), step(2, 'done'), step(3, 'pending')], null)).toBe(2);
  });

  it('step_id 不在计划内时同样回落', () => {
    expect(currentStepIndex([step(1, 'done')], 99)).toBe(1);
  });
});

describe('ClarificationPart.answeredCount', () => {
  it('只统计非空回答', () => {
    expect(answeredCount(['甲', '', '  ', '乙'])).toBe(2);
    expect(answeredCount([])).toBe(0);
  });
});
