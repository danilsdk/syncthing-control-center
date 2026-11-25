import type { AnalyticsSummary } from "./types";

const percentage = new Intl.NumberFormat("ru-RU", {
  style: "percent",
  maximumFractionDigits: 1,
});
export const formatRatio = (ratio: number | null) =>
  ratio === null ? "Нет данных" : percentage.format(ratio);

export function DailyMetrics({ daily }: { daily: AnalyticsSummary["daily"] }) {
  return (
    <section className="panel">
      <h2>По дням (UTC)</h2>
      <div className="analytics-table">
        <table aria-label="Дневные показатели UTC">
          <thead>
            <tr>
              <th scope="col">Дата</th>
              <th scope="col">Команды</th>
              <th scope="col">Проверки API</th>
            </tr>
          </thead>
          <tbody>
            {daily.map(({ date, commands, api_checks }) => (
              <tr key={date}>
                <th scope="row">
                  <time dateTime={date}>{date}</time>
                </th>
                <td>
                  <strong>{formatRatio(commands.success_ratio)}</strong>
                  <small>
                    {commands.applied} из{" "}
                    {commands.applied + commands.failed}
                  </small>
                </td>
                <td>
                  <strong>{formatRatio(api_checks.success_ratio)}</strong>
                  <small>
                    {api_checks.successful_checks} из{" "}
                    {api_checks.total_checks}
                  </small>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
