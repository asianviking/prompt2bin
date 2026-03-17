	.file	"tmpwg3jhxhz.c"
	.intel_syntax noprefix
	.text
	.p2align 4
	.globl	_force_create
	.type	_force_create, @function
_force_create:
	endbr64
	sub	rsp, 8
	mov	esi, 65536
	mov	edi, 8
	call	aligned_alloc@PLT
	test	rax, rax
	je	.L1
	pxor	xmm0, xmm0
	movups	XMMWORD PTR [rax], xmm0
.L1:
	add	rsp, 8
	ret
	.size	_force_create, .-_force_create
	.p2align 4
	.globl	_force_alloc
	.type	_force_alloc, @function
_force_alloc:
	endbr64
	test	rdi, rdi
	je	.L11
	test	rsi, rsi
	je	.L11
	mov	rax, QWORD PTR [rdi]
	add	rax, 7
	and	eax, 4294967288
	add	rsi, rax
	cmp	rsi, 65520
	ja	.L11
	mov	QWORD PTR [rdi], rsi
	lea	rax, 16[rdi+rax]
	ret
	.p2align 4,,10
	.p2align 3
.L11:
	xor	eax, eax
	ret
	.size	_force_alloc, .-_force_alloc
	.p2align 4
	.globl	_force_reset
	.type	_force_reset, @function
_force_reset:
	endbr64
	test	rdi, rdi
	je	.L12
	add	QWORD PTR 8[rdi], 1
	mov	QWORD PTR [rdi], 0
.L12:
	ret
	.size	_force_reset, .-_force_reset
	.p2align 4
	.globl	_force_destroy
	.type	_force_destroy, @function
_force_destroy:
	endbr64
	test	rdi, rdi
	je	.L17
	jmp	free@PLT
	.p2align 4,,10
	.p2align 3
.L17:
	ret
	.size	_force_destroy, .-_force_destroy
	.ident	"GCC: (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0"
	.section	.note.GNU-stack,"",@progbits
	.section	.note.gnu.property,"a"
	.align 8
	.long	1f - 0f
	.long	4f - 1f
	.long	5
0:
	.string	"GNU"
1:
	.align 8
	.long	0xc0000002
	.long	3f - 2f
2:
	.long	0x3
3:
	.align 8
4:
