import React from "react";
import { AbsoluteFill } from "remotion";
import { AccountsTable } from "../components/AccountsTable";
import { Caption } from "../components/Caption";
import { Terminal, typingEnd, type TermEntry } from "../components/Terminal";
import { agentTask, hookCaption, wrongSql, wrongSqlResult } from "../data";
import { colors } from "../theme";

export const HOOK_FRAMES = 200;

const sqlCmd = {
  kind: "cmd" as const,
  at: 4,
  text: wrongSql,
  speed: 1.1,
  prefix: [{ t: "sql> ", c: colors.textMuted }],
};
const typed = typingEnd(sqlCmd);
const DAMAGE_AT = typed + 10;

const entries: TermEntry[] = [
  { kind: "line", at: -5, segs: [{ t: agentTask, c: colors.textDim }] },
  sqlCmd,
  { kind: "line", at: typed + 6, segs: [{ t: wrongSqlResult, c: colors.red, b: true }] },
];

export const Hook: React.FC = () => (
  <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
    <Terminal
      entries={entries}
      title="agent · execute_sql"
      width={1440}
      rows={3}
      fontSize={36}
      lineHeight={54}
      style={{ marginTop: -20 }}
    />
    <AccountsTable width={1440} damageAt={DAMAGE_AT} showTarget style={{ marginTop: 56 }} />
    <Caption
      lines={[hookCaption[0], { text: hookCaption[1], color: colors.red }]}
      at={DAMAGE_AT + 22}
      stagger={14}
      style={{ marginTop: 64 }}
    />
  </AbsoluteFill>
);
