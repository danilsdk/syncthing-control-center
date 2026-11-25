const eventLabels = new Map([
  ["ItemStarted", "Начата обработка элемента"],
  ["ItemFinished", "Обработка элемента завершена"],
  ["StateChanged", "Изменилось состояние папки"],
  ["HistoryGap", "Пропуск в истории"],
  ["SourceRestarted", "Syncthing перезапущен"],
  ["SourceContinuityLost", "Непрерывность истории не подтверждена"],
]);

export const eventName = (type:string) => eventLabels.get(type) ?? type;
