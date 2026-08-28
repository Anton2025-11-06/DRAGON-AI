import config from '.';

export default {
  plugins: {
    ...(process.env.NODE_ENV === 'production' ? { cssnano: {} } : {}),
    // Specifying the config is not necessary in most cases, but it is included
    autoprefixer: {},
    // 修复 element-plus 和 ant-design-vue 的样式和tailwindcss冲突问题
    'postcss-antd-fixes': { prefixes: ['ant', 'el'] },
    'postcss-import': {},
    // 禁用 :is() 选择器转换，避免 markstream-vue 等库的复杂选择器警告
    'postcss-preset-env': {
      features: {
        'is-pseudo-class': false,
      },
    },
    tailwindcss: { config },
    'tailwindcss/nesting': {},
  },
};
