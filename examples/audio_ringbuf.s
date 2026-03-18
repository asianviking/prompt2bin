	.file	"tmp59kcvut0.c"
	.intel_syntax noprefix
	.text
	.p2align 4
	.globl	_force_create
	.type	_force_create, @function
_force_create:
	endbr64
	push	rbx
	mov	edi, 16512
	call	malloc@PLT
	mov	rbx, rax
	test	rax, rax
	je	.L1
	mov	QWORD PTR [rax], 0
	lea	rdi, 128[rax]
	mov	edx, 16384
	xor	esi, esi
	mov	QWORD PTR 64[rax], 0
	call	memset@PLT
.L1:
	mov	rax, rbx
	pop	rbx
	ret
	.size	_force_create, .-_force_create
	.p2align 4
	.globl	_force_push
	.type	_force_push, @function
_force_push:
	endbr64
	mov	rax, QWORD PTR [rdi]
	mov	rcx, QWORD PTR 64[rdi]
	mov	rdx, rax
	sub	rdx, rcx
	cmp	rdx, 4095
	ja	.L10
	mov	ecx, DWORD PTR [rsi]
	mov	rdx, rax
	add	rax, 1
	and	edx, 4095
	mov	DWORD PTR 128[rdi+rdx*4], ecx
	mov	QWORD PTR [rdi], rax
	xor	eax, eax
	ret
	.p2align 4,,10
	.p2align 3
.L10:
	mov	eax, -1
	ret
	.size	_force_push, .-_force_push
	.p2align 4
	.globl	_force_pop
	.type	_force_pop, @function
_force_pop:
	endbr64
	mov	rax, QWORD PTR 64[rdi]
	mov	rdx, QWORD PTR [rdi]
	cmp	rax, rdx
	je	.L13
	mov	rdx, rax
	add	rax, 1
	and	edx, 4095
	mov	edx, DWORD PTR 128[rdi+rdx*4]
	mov	DWORD PTR [rsi], edx
	mov	QWORD PTR 64[rdi], rax
	xor	eax, eax
	ret
	.p2align 4,,10
	.p2align 3
.L13:
	mov	eax, -1
	ret
	.size	_force_pop, .-_force_pop
	.p2align 4
	.globl	_force_destroy
	.type	_force_destroy, @function
_force_destroy:
	endbr64
	test	rdi, rdi
	je	.L14
	jmp	free@PLT
	.p2align 4,,10
	.p2align 3
.L14:
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
