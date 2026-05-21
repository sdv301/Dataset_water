/** Браузерные уведомления по завершении обучения (без перезагрузки страницы). */

export async function requestTrainingNotifications(): Promise<boolean> {
  if (typeof window === 'undefined' || !('Notification' in window)) {
    return false;
  }
  if (Notification.permission === 'granted') return true;
  if (Notification.permission === 'denied') return false;
  const result = await Notification.requestPermission();
  return result === 'granted';
}

export function notifyTrainingFinished(success: boolean, message: string): void {
  const title = success ? 'HydroPredict — обучение завершено' : 'HydroPredict — ошибка обучения';
  const body = message || (success ? 'Модель готова. Обновите страницу (F5).' : 'См. сообщение в приложении.');

  if (typeof window !== 'undefined' && 'Notification' in window && Notification.permission === 'granted') {
    try {
      const n = new Notification(title, {
        body,
        tag: 'hydropredict-training',
        requireInteraction: true,
      });
      n.onclick = () => {
        window.focus();
        n.close();
      };
    } catch {
      /* ignore */
    }
  }
}
