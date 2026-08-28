import type {
  ColumnCompositionProps,
  ValueBuilderContext,
} from '@fast-crud/fast-crud';
import type { FsEditorWang5Config } from '@fast-crud/fast-extends/dist/d/editor/type/config';

import type { App } from 'vue';

import { computed } from 'vue';

import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import {
  FastCrud,
  registerMergeColumnPlugin,
  useTypes,
} from '@fast-crud/fast-crud';
import {
  FsExtendsCopyable,
  FsExtendsEditor,
  FsExtendsJson,
  FsExtendsTime,
  FsExtendsUploader,
} from '@fast-crud/fast-extends';
import ui from '@fast-crud/ui-antdv4';
import Antdv, { notification } from 'ant-design-vue';
import dayjs from 'dayjs';

import { defHttp } from '#/api/request';
import { columnSizeSaver } from '#/plugin/fast-crud/column-size-saver';

import { useCrudPermission } from './setup-fast-crud-permission';

import '@fast-crud/fast-crud/dist/style.css';
import '@fast-crud/ui-antdv4/dist/style.css';
import '@fast-crud/fast-extends/dist/style.css';
import './setup-fast-crud.less';

// import type _ from "lodash-es";

export function registerFastCrud(app: App) {
  app.use(Antdv);
  app.use(ui);
  app.use(FastCrud, {
    // i18n,
    logger: { off: { tableColumns: false } },
    async dictRequest({ url }: any) {
      return await defHttp.request(url, {});
    },
    commonOptions(props: any) {
      const { crudExpose } = props;
      const crudBinding = crudExpose?.crudBinding;
      const opts = {
        pagination: {
          pageSize: 10,
          currentPage: 1,
          show: true,
          showSize: true,
          // 可选的页面大小选项
          pageSizes: [10, 20, 50, 100],
        },
        toolbar: {
          // toolbar.buttons.export.show:false 显示隐藏
          // toolbar.compact:false 默认选择
          compact: false,
          buttons: {
            compact: { show: false },
          },
        },
        search: {
          buttons: {
            search: {
              style: 'marginLeft:-2px',
            },
          },
        },
        actionbar: {
          buttons: {
            add: {
              icon: 'akar-icons:circle-plus',
            },
          },
        },
        container: {
          is: 'fs-layout-card',
        },
        rowHandle: {
          width: 180,
          align: 'left',
          // 固定右侧 不建议设置成全局
          fixed: 'right',
          buttons: {
            view: { order: 1, size: 'small', type: 'link', icon: null },
            edit: { order: 2, size: 'small', type: 'link', icon: null },
            remove: {
              order: 3,
              size: 'small',
              type: 'link',
              icon: '',
              // 重写 render 默认的 confirm 没有 pop-confirm 操作友好
              render(scope: any) {
                function confirm() {
                  const { row, index } = scope;
                  crudExpose.doRemove({ row, index }, { noConfirm: true });
                }

                return (
                  <a-popconfirm
                    cancel-text="取消"
                    ok-text="确认删除"
                    onConfirm={confirm}
                    placement="bottom"
                    title={'确定要删除这条记录吗'}
                    v-slots={{
                      icon: () => (
                        <QuestionCircleOutlined style={{ color: 'red' }} />
                      ),
                    }}
                  >
                    <fs-button class="ant-btn-sm" danger type="link">
                      删除
                    </fs-button>
                  </a-popconfirm>
                );
              },
            },
          },
          dropdown: { more: { type: 'link' } },
        },
        table: {
          size: 'small',
          scroll: {
            // 需要设置它，否则滚动条拖动时，表头不会动
            fixed: false,
          },
          pagination: false,
          onResizeColumn: (w: number, col: any) => {
            if (
              crudBinding.value?.table?.columnsMap &&
              crudBinding.value?.table?.columnsMap[col.key]
            ) {
              crudBinding.value.table.columnsMap[col.key].width = w;
              columnSizeSaver.save(col.key, w);
            }
          },
        },
        request: {
          transformQuery: ({ page, form, sort }: any) => {
            const order =
              sort === null ? {} : { column: sort.prop, asc: sort.asc };
            const currentPage = page.currentPage ?? 1;
            const limit = page.pageSize ?? 10;
            // 本项目后端统一分页参数：page / page_size
            return {
              page: currentPage,
              page_size: limit,
              ...form,
              ...order,
            };
          },
          transformRes: ({ res }: any) => {
            // 兼容本项目后端分页结构 {total, items} 与历史结构 {records, total}
            const data = res?.data ?? res ?? {};
            const result: any = {
              pageSize: Number.parseInt(
                data.page_size ?? data.size ?? 10,
              ),
              total: Number.parseInt(data.total ?? 0),
              records: data.items ?? data.records ?? [],
            };
            // 仅当后端明确返回 page/current 字段时才覆盖当前页码，
            // 否则保留分页器当前页码（避免翻页后回退到第 1 页）
            if (data.page !== undefined && data.page !== null) {
              result.currentPage = Number.parseInt(data.page);
            }
            if (data.current !== undefined && data.current !== null) {
              result.currentPage = Number.parseInt(data.current);
            }
            return result;
          },
        },
        form: {
          // 关闭表单草稿记忆：避免打开“添加”表单时回填上次输入的数据
          saveDraft: false,
          display: 'flex',
          wrapper: {
            buttons: {
              paste: {
                show: false,
              },
              copy: {
                show: false,
              },
            },
            copyRemind: false,
            is: 'a-drawer',
            saveRemind: true,
          },
          async afterSubmit({ mode }: any) {
            if (mode === 'add') {
              // notification.success({ message: '添加成功' });
            } else if (mode === 'edit') {
              notification.success({ message: '保存成功' });
            }
          },
          wrapperCol: { span: null },
          labelCol: {
            // 固定label宽度
            span: null,
            style: { minWidth: '100px' },
          },
          layout: computed(() => {
            // return getLocale.value === LOCALE.ZH_CN ? "horizontal" : "vertical";
            return 'horizontal';
          }),
        },
      };
      const permission = props.context?.permission || null;
      const crudPermission = useCrudPermission({ permission });
      return crudPermission.merge(opts);
    },
  } as any);

  // 安装editor
  app.use(FsExtendsEditor, {
    // 编辑器的公共配置
    wangEditor5: {
      editorConfig: {
        MENU_CONF: {},
      },
      toolbarConfig: {},
    } as FsEditorWang5Config,
  });
  app.use(FsExtendsJson);
  app.use(FsExtendsTime);
  app.use(FsExtendsCopyable);
  // app.use(FsExtendsInput);
  // 配置uploader 公共参数
  app.use(FsExtendsUploader, {
    defaultType: 'form',
    form: {
      // 本项目后端暂无文件上传接口，先指向网关统一前缀，后续由后端补充
      action: '/api/system/files/upload',
      name: 'file',
      withCredentials: false,
      uploadRequest: async ({ action, file, onProgress }: any) => {
        const data = new FormData();
        data.append('file', file);
        return await defHttp.post(action, data, {
          headers: {
            'Content-Type': 'multipart/form-data',
          },
          timeout: 60_000,
          onUploadProgress: (p: any) => {
            onProgress({ percent: Math.round((p.loaded / p.total) * 100) });
          },
        });
      },
      successHandle(ret: any) {
        // 上传完成后的结果处理：兼容后端 ApiResponse {code, message, data}
        // 此后台返回的结果应该为 data = { url, ... }
        const result = ret?.data ?? ret;
        if (!result?.url) {
          throw new Error('上传失败');
        }
        return { url: result.url, fileId: result.url, key: result.url };
      },
    },
  } as any);
  initColumnSetting();

  function initColumnSetting() {
    // 修改官方字段类型
    // 不要写在页面里，这个是全局的，要写在vue.use(FastCrud)之后
    const { getType, addTypes } = useTypes();
    const selectType = getType('dict-select');
    if (selectType && selectType.column && selectType.column.component) {
      // 修改官方的字段类型，设置为支持自动染色
      selectType.column.component.color = 'auto';
    }
    addTypes({
      'wp-readonly-time': {
        column: { width: 170, align: 'center' },
        addForm: { show: false },
        editForm: { show: false },
        valueBuilder({ value, row, key }: ValueBuilderContext): void {
          if (value !== null) {
            row[key] = dayjs(value);
          }
        },
      },
    });
  }

  // 默认宽度，支持自动拖动调整列宽
  registerMergeColumnPlugin({
    name: 'resize-column-plugin',
    order: 2,
    handle: (columnProps: ColumnCompositionProps) => {
      if (!columnProps.column) {
        columnProps.column = {};
      }
      if (
        columnProps.column?.resizable === undefined ||
        columnProps.column.resizable === null
      ) {
        columnProps.column.resizable = true;
        const savedColumnWidth = columnSizeSaver.get(columnProps.key as string);
        if (savedColumnWidth) {
          columnProps.column.width = savedColumnWidth;
        } else if (!columnProps.column.width) {
          columnProps.column.width = 200;
        }
      }

      return columnProps;
    },
  });
}
