/**
 * Пиксельный фильтр для иконок — тот же приём, что на экране телефона.
 *
 * Картинка ужимается до маленького холста и растягивается обратно с
 * выключенным сглаживанием. Пиксельность берётся из низкого разрешения, а не
 * из эффекта поверх: так иконки сервисов не спорят стилистически с 3D-экраном
 * и пиксельными заголовками.
 *
 * Результат кешируется: один и тот же файл обрабатывается однажды на всю
 * сессию, сколько бы раз он ни появился в списке.
 */

const cache = new Map<string, Promise<string>>();

/** Сторона внутреннего холста. Больше — глаже, а нам нужна именно крупность. */
const GRID = 24;

export function pixelateIcon(src: string, grid = GRID): Promise<string> {
  const key = `${src}@${grid}`;
  const hit = cache.get(key);
  if (hit) return hit;

  const task = new Promise<string>((resolve, reject) => {
    const image = new Image();
    image.crossOrigin = "anonymous";
    image.onload = () => {
      const small = document.createElement("canvas");
      small.width = grid;
      small.height = grid;
      const sctx = small.getContext("2d")!;
      sctx.imageSmoothingEnabled = false;
      sctx.drawImage(image, 0, 0, grid, grid);

      // Обратно растягиваем без сглаживания — так получаются честные квадраты.
      const out = document.createElement("canvas");
      const size = grid * 4;
      out.width = size;
      out.height = size;
      const octx = out.getContext("2d")!;
      octx.imageSmoothingEnabled = false;
      octx.drawImage(small, 0, 0, size, size);
      resolve(out.toDataURL("image/png"));
    };
    image.onerror = () => reject(new Error(`иконка не загрузилась: ${src}`));
    image.src = src;
  });

  cache.set(key, task);
  return task;
}

/**
 * Подставить обработанную иконку в элемент.
 *
 * Пока иконка не готова (или если её нет), остаётся заглушка из CSS —
 * пустое место в списке хуже, чем кружок.
 */
export function applyPixelIcon(node: HTMLElement, src: string): void {
  void pixelateIcon(src)
    .then((data) => {
      node.style.backgroundImage = `url(${data})`;
      node.dataset.pixelated = "";
    })
    .catch(() => {
      /* заглушка остаётся; ошибка не должна ронять список */
    });
}
